# 一个 RISC-V 控制 CPU + 四个 RVV Coral

这个实验集成真实 PicoRV32 RV32IM 控制核、四个启用 RVV 的 Coral CoreAxi、64 KiB 控制核 RAM、16 KiB 共享 SRAM，以及两级 AXI 仲裁。CPU 执行固件完成 kernel 加载、分片、启动、同步和异常检查；C++ 测试台只装入控制 CPU 启动镜像并独立读取结果。

Coral 当前为 VLEN=128 bit，设备代码使用 e32,m1 的整数 RVV 指令。算子包括向量加法、GEMM+bias+ReLU、3×3 卷积+bias+ReLU。使用现有 Clang/LLD 与 `riscv_vector.h`，没有为本实验修改 LLVM；没有启用 Zvt 矩阵引擎。

这是功能 RTL 原型。控制 CPU 是轻量 RV32IM 核，尚无完整模型推理、DMA/HBM、缓存一致性、3D 堆叠、多卡或物理设计签核。

## 建模分支的统一入口

专用分支为 `feat/ncoral-model`，基于 `feat/ncoral` 的四核 RVV 原型。先阅读 [完整编译与运行说明](docs/build_and_run.md) 和 [架构、已完成范围与后续任务](docs/model_status.md)。

```bash
python3 experiments/multicore/tools/ncoral.py doctor
python3 experiments/multicore/tools/ncoral.py build --jobs 2
python3 experiments/multicore/tools/ncoral.py run
python3 experiments/multicore/tools/ncoral.py demo
python3 experiments/multicore/tools/ncoral.py test --jobs 3
```

`build-sim` 可只构建 RTL 仿真器，`build-code` 可只编译 Coral kernel 和控制固件。统一入口的 `run/demo/test` 复用构建产物，修改后先重建对应部分。代码编译会保留 kernel LLVM IR、汇编、ELF/bin、链接 map 和三套程序的反汇编；报告记录完整编译命令。下面的原始脚本入口继续保留。

## 运行

在仓库根目录执行，需要 Python 3、GCC/G++、Clang/LLD 17、llvm-objcopy/llvm-objdump、zlib 开发包和 LZ4 运行库。脚本使用校验过的 Bazel 下载及上游 Chisel/Verilator 依赖，首次构建需要网络与足够内存。

```bash
# 首次：编译设备/控制固件、生成 RTL、构建仿真器，演示串行与并行。
python3 experiments/multicore/tools/die/demo_complex.py

# 代码与构建结果未变化时复用产物。
python3 experiments/multicore/tools/die/demo_complex.py --skip-build --skip-firmware

# 默认四核 RVV 加法，或连续两个有依赖的 RVV 加法。
python3 experiments/multicore/tools/die/run.py --skip-build --skip-firmware
python3 experiments/multicore/tools/die/run.py --skip-build --skip-firmware --rounds 2

# 19 项单 die 回归：先完成上述构建。
python3 experiments/multicore/tools/die/check.py

# 可选：同时运行三个独立仿真实例，每个实例仍是一颗四核 die。
python3 experiments/multicore/tools/die/check.py --jobs 3
```

默认构建上限 1800 秒；复杂算子仿真每种方式上限 420 秒；普通运行上限 60 秒。回归中的普通用例每项 120 秒，复杂算子用例每项 420 秒。每阶段先打印绝对截止时间，超时终止进程组并报告失败；错误注入用例须命中指定错误路径才能算通过。`--jobs` 仅控制独立测试实例的宿主并发数，不改变芯片内核数或算子调度。

默认工具缓存位于 `tools/.tools/`，结果位于 `tools/die/logs/`，均不纳入版本管理。`NCORAL_WORK_DIR=/absolute/path` 可将缓存和日志迁到指定目录，其下生成 `.tools/`、`die/logs/` 和 `multicore/logs/`。源码和 Bazel 工作目录始终从当前仓库位置解析，不依赖开发者的绝对路径。

`--skip-build` 只跳过 RTL 构建；修改硬件后应重建。修改 kernel 后应重新编译固件。`--skip-firmware` 复用时仍校验 RVV 指令证据与镜像哈希，避免运行旧标量镜像。

## 两个复杂算子如何调度

默认 GEMM 输出为 7×12，归约 K=24；卷积输入为 NHWC 1×10×10×2，权重为 `[Co][Ky][Kx][Ci]`，Co=4、kernel=3×3、stride=1、padding=0，计算前 7 个输出行，输出为 1×7×8×4。两算子使用独立数据，可以并行。

核 0/1 运行 GEMM，核 2/3 运行卷积，组内按输出行分为 4+3。两种演示保持相同核分配，只改变控制 CPU 的提交顺序：

```text
串行：start(0x3) → wait(0x3) → start(0xc) → wait(0xf)
并行：start(0x3) → start(0xc) → wait(0xf)
```

GEMM 沿输出列向量化，卷积沿输出通道向量化，使用向量乘加和向量 ReLU。卷积先把共享权重打包到每核私有 DTCM，再做连续向量加载。LLVM 可以将部分 `vmacc.vx` 合法改写为 `vmadd.vx`，硬件派发计数同时识别两者。

提交顺序与核组当前在固件中手写。没有通用图调度器、任务队列或自动资源分配。存在数据依赖的算子需要等待前驱；原加法示例 `C=A+B`、`D=C+B` 明确串行执行。

## 核内打印与正确性

kernel 的 `coral_log()` 向 `0x20003ff0` 执行真实 MMIO 写。RTL 在 AXI 写握手时加硬件周期时间戳，测试台格式化为核号、算子、START、RVV_CONFIG、ROW_DONE 和 END。共享地址空间最后 16 字节保留给日志端点。

编译阶段检查反汇编中实际存在向量加载、存储、乘加与激活指令；运行阶段要求各核报告 VLEN=128、e32,m1 的 VL=4。集群从现有 debug dispatch 端口统计真正握手的 `vmacc.vx/vmadd.vx`，逐核检查次数；这是派发统计，不是架构退休统计。数值检查通过独立 int64 参考实现逐元素完成，并核对输入不变、保护字、done/WFI、fault、mcause 和 checksum。

串行必须先结束 GEMM 再开始卷积；并行必须有重叠。默认 7 行与满 8 行并行用例还要求双方都在另一算子活动时完成过输出行。演示脚本额外核对串行与并行的完整输出数组完全相同。小尺寸用例覆盖空分片，向量加法覆盖尾段和非 16B 对齐分片。

日志和 fence 会消耗实际执行资源；本实验用于正确性与调度观察，不以日志周期宣称加速比。详细实现和本轮实测结果见 [RVV 验证说明](docs/rvv_execution.md)。

## 代码地图

- [Cluster.scala](Cluster.scala)：2/4 核簇、共享 AXI 仲裁和可选 RVV 派发观察。
- [die/Die.scala](die/Die.scala)：真实控制 CPU、启动 RAM、MMIO 桥、共享 SRAM 和日志端点。
- [device/complex.c](device/complex.c)：RVV GEMM/卷积及打印。
- [device/add.c](device/add.c)：RVV 加法，兼容早期独立标量簇编译。
- [die/firmware/runtime.c](die/firmware/runtime.c)：控制核上的加载、分片、核组启动与等待。
- [die/firmware/complex_demo.c](die/firmware/complex_demo.c)：两个独立算子的提交计划。
- [die/smoke_die.cc](die/smoke_die.cc)、[die/complex_check.h](die/complex_check.h)：独立只读检查、数值参考和执行证据验证。
- [tools/die/](tools/die/)：固件构建、单 die 运行、复杂算子演示与 19 项回归。
- [tools/multicore/](tools/multicore/)：共享工具依赖及早期独立簇运行/验证入口。

`smoke_scalar`、`smoke_rvv`、`smoke_quad_scalar`、`smoke_quad_rvv` 保留早期测试台 runtime 实验；当前真实控制 CPU 的主入口是 `smoke_die`。PicoRV32 固定源码、ISC 许可证与哈希保存在 [vendor 目录](die/vendor/picorv32/)，构建脚本检查来源完整性。原 Coral 执行核源码保持原样，实验通过参数和集成代码启用 RVV。
