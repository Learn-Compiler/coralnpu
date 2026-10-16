# 四 Coral 的 RVV 算子执行

本次把单 die 默认配置从四个标量 Coral 切换到四个启用 RVV 的 Coral。控制核仍是 RV32IM PicoRV32，负责加载、分片和同步。Coral 执行算子的乘加、激活及向量访存。2026-10-16：两个复杂算子的 RVV 串行与并行演示均通过，全部输出逐元素相同，19 项回归全部通过。

本分支包含完整单 die RTL、设备 kernel、控制固件与构建/回归代码。历史标量记录不作为 RVV 通过证据。机器可读结果见 [verification.json](verification.json)。

## 如何运行

在 `仓库根目录`：

```bash
# 首次或修改了 RTL：编译固件、构建四核 RVV RTL，演示两个复杂算子。
python3 experiments/multicore/tools/die/demo_complex.py --build-timeout 1800 --run-timeout 420

# 固件及 RTL 均未变化：复用构建结果。
python3 experiments/multicore/tools/die/demo_complex.py --skip-build --skip-firmware --run-timeout 420

# 默认四核 RVV 加法。
python3 experiments/multicore/tools/die/run.py --skip-build --skip-firmware

# 19 项回归，包括 RVV 尾段、非 16B 对齐分片、空分片和串并行。
python3 experiments/multicore/tools/die/check.py
```

工具每阶段先打印绝对截止时间，再运行和检查结束状态。复杂算子日志位于 `tools/die/logs/complex/rows7_stall3/`；各核事件立即刷新到日志文件，演示脚本在每种模式结束后回放。kernel 事件逐条刷新到日志。

复用固件时会校验 RVV 编译报告、必需向量指令的反汇编证据以及镜像 SHA256；旧标量镜像不能作为 RVV 验证输入。

## 硬件与编译目标

`Die.scala` 实例化 `CoralCluster(4, true, observeRvv=true)`；生成使用 `RVV_CORE_MINI_AXI_VOPTS`，包含 `VLEN_128`、`ZVE32F_ON` 等上游所需选项。各核 VLEN=128 bit，kernel 使用 e32,m1，一次最多处理 4 个 int32 元素。

设备编译目标为 `rv32im_zicsr_zve32x_zvl128b`、ilp32；控制固件目标保持 `rv32im`、ilp32。目标选择只要求本实验使用的整数 RVV 能力，没有启用 Zvt 矩阵引擎。启动汇编在设备以 RVV 目标编译时设置 `mstatus.VS`。

仍使用系统 Clang 17/LLD，无需修改 LLVM。`-fno-vectorize` 和 `-fno-slp-vectorize` 只关闭自动向量化；代码显式调用 `riscv_vector.h` 中的 intrinsic，会直接生成相应 RVV 指令。

## 算子怎样使用 RVV

| 计算 | 实现 |
|---|---|
| 加法 | 动态 `vsetvl`，两个 `vle32.v`，`vadd.vv`，`vse32.v`；保留标量编译分支用于旧独立簇实验 |
| GEMM+bias+ReLU | 沿 N 维向量化，加载 bias 与连续权重列，广播标量 A，通过 `vmacc.vx` 累加，`vmax.vx` 实现 ReLU，`vse32.v` 保存 |
| 3×3 卷积+bias+ReLU | 沿输出通道向量化，标量输入乘向量权重，通过 `vmacc.vx` 累加；ReLU 与结果存储同上 |

共享权重布局和算子数学定义保持 原始算子接口合同。卷积共享权重为 `[Co][Ky][Kx][Ci]`；各卷积核先用 `vlse32.v` 跨通道取数，打包到自己 DTCM 栈上的 `[tap][Co]` 缓冲，然后内层循环用连续的向量加载。缓冲为 288 字节、16B 对齐，核间不共享这块 scratch；不需要动态分配或全局数据段加载。

kernel 中仍有标量循环、地址计算、标量输入读取和日志操作。它们与 RVV 数据计算配合，是实际设备程序的一部分。数据仍是小幅值 int32，累加无溢出，不是 BF16/INT8 量化实现。

## 核组与算子调度

仍然是核 0/1 运行 GEMM，核 2/3 运行卷积，各组按输出行分片。两个算子输入和输出相互独立。

串行由 runtime 执行 `start(0x3) → wait(0x3) → start(0xc) → wait(0xf)`；并行执行 `start(0x3) → start(0xc) → wait(0xf)`。LLVM 负责核内代码生成，控制 CPU 上的 runtime 决定核组和开始时机；不存在为了多核调度新增 LLVM 指令的问题。

## 怎样证明真正走了 RVV

验证由三个层次共同组成：

1. **镜像指令检查**：构建保存 `coral_disassembly.log`、`complex_disassembly.log`，缺少指定 `vle32.v`、`vse32.v`、`vmacc.vx`、`vmax.vx` 等指令时直接构建失败。报告列出逐项检查结果。
2. **运行时向量配置**：每核打印 `RVV_CONFIG vlenb=16 vl(e32,m1)=4`。这些值通过实际 CSR 读取及 `vsetvl` 返回值产生；空任务核也要正确配置并报告。
3. **硬件派发计数与数值结果**：集群通过 CoreAxi 已有 debug dispatch 端口统计真正握手的 `vmacc.vx` 与 `vmadd.vx`。LLVM 可以交换目标寄存器约束，在卷积中生成后者；两者均实现向量乘加。不是统计取指，也不是架构退休计数。每个非空核必须达到精确预期次数，随后还要通过无 fault/trap、done/WFI 和全部输出检查。

计数器只在全局复位时清零，当前复杂场景各核只启动一次。WFI/halted 核不会因调试信号停留而继续累计；没有开启完整 ROB verification 模式，也没有修改上游执行核文件。

默认 rows=7 分为 4+3 行，e32,m1、VLEN=128 时预期的向量乘加派发次数为：

```text
Coral0 GEMM：4 × (12/4) × 24 = 288
Coral1 GEMM：3 × (12/4) × 24 = 216
Coral2 Conv：4 × 8 × 18 × (4/4) = 576
Coral3 Conv：3 × 8 × 18 × (4/4) = 432
```

最终 JSON 包含 `compute_backend: RVV`、`vlen_bits: 128`、`rvv_mac_dispatched`。原有独立 int64 参考、输入不变、保护字、输出逐元素相等以及串并行顺序/进度检查继续适用。打印新增事件 4，detail 高字节为 vlenb、低字节为 VL；其余事件编码保持原样。

## 代码入口

- [complex.c](../device/complex.c)：显式 RVV GEMM、卷积与核内打印。
- [add.c](../device/add.c)：RVV 加法及旧簇标量兼容分支。
- [Cluster.scala](../Cluster.scala)：RVV 配置与实际派发计数器。
- [Die.scala](../die/Die.scala)：四核 RVV 集群与控制 CPU 集成。
- [complex_check.h](../die/complex_check.h)：独立数值参考、配置与计数检查。
- [build_firmware.py](../tools/die/build_firmware.py)：双目标编译、反汇编与镜像校验。

核内 SIMD 和多核并行是两层不同的并行。是否更快还取决于访问布局、共享端口仲裁、打印和调度开销；本轮验收是 RVV 路径与功能正确性，不给出性能结论。

## 本轮实测结果

默认 rows=7 的 GEMM 84 个输出、卷积 224 个输出全部通过独立 int64 参考；串行与并行完整输出数组再次交叉检查，逐元素相同。两种方式四核向量乘加派发次数均为 `[288, 216, 576, 432]`，全部符合预期。

串行最后一个 GEMM END 为周期 201617，卷积 START 为周期 202067。并行日志中的实际片段：

```text
[cycle 194458] Coral0 GEMM+bias+ReLU START first_row=0
[cycle 194482] Coral0 GEMM+bias+ReLU RVV_CONFIG vlenb=16 vl(e32,m1)=4
[cycle 194678] Coral2 Conv3x3+bias+ReLU START first_row=0
[cycle 199394] Coral2 Conv3x3+bias+ReLU ROW_DONE row=0
[cycle 204308] Coral0 GEMM+bias+ReLU END rows=4
```

并行期间 GEMM 的 7 行、卷积的 4 行在另一算子仍活动时完成。19 项回归全部通过：保留 13 项加法、依赖、异常及截止路径，新增 rows=1/3/8 各自的串行/并行共 6 项复杂算子用例；rows=8 使用 stall-period=5，输出 96+256 个元素，派发次数 `[288, 288, 576, 576]`。

四核 RVV 首次模型构建约 19 分钟，后续运行复用构建缓存。早期 180 秒宿主预算下有一次并行运行在四核 END 后超时，未计入通过结果；调整为 420 秒宿主预算后完整验收通过。芯片内等待预算和全部数值/顺序检查未放宽。版本管理仅保存验证摘要与源码哈希，工具缓存和完整日志留在本地。
