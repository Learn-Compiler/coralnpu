# 编译与运行：一个控制 CPU + 四个 Coral

本建模分支为 `feat/ncoral-model`，从已验证的 `feat/ncoral`（`ac808307`）继续开发。所有代码位于 `experiments/multicore/`。以下命令均在 **coralnpu 仓库根目录**执行，统一入口是 `tools/ncoral.py`，无需依赖仓库旁的旧脚本。

## 1. 环境与第一次运行

支持 Linux x86_64，Python 3.9+。已验证代码编译器为 Clang/LLD 17，需要 GCC/G++、make、LLVM objcopy/objdump、系统 zlib 开发包和 LZ4 运行库。Debian/Ubuntu 可按需安装：

```bash
sudo apt-get install python3 gcc g++ make clang-17 lld-17 llvm-17 zlib1g-dev liblz4-1
```

不同发行版的软件源不一定提供相同版本；`build-code --clang /path/to/clang --lld /path/to/ld.lld` 可指定编译器，objcopy/objdump 需在 PATH 中。`doctor` 检查工具和本地文件是否存在，不代表编译、依赖下载或运行已经通过。

```bash
python3 experiments/multicore/tools/ncoral.py doctor

# 编译两个 Coral kernel、控制核固件和 RTL 仿真器。
python3 experiments/multicore/tools/ncoral.py build --jobs 2

# 运行四核向量加法；run/demo 均只使用已构建产物。
python3 experiments/multicore/tools/ncoral.py run

# 两个复杂算子：串行、并行各运行一次，比较全部输出。
python3 experiments/multicore/tools/ncoral.py demo

# 19 项回归；jobs 是宿主测试进程数，不是 Coral 核数。
python3 experiments/multicore/tools/ncoral.py test --jobs 3
```

首次构建需要网络下载 Bazel、Chisel、firtool、Verilator 等上游依赖；无需另装系统 Java/Verilator。脚本固定并校验 Bazel 9.1.0，它与根 README 的通用上游构建命令是两套入口。本实验通过已有依赖覆盖处理上游旧 zlib 下载地址，建议使用这里的脚本。首次四核 RVV RTL 构建在当前机器上曾约 19 分钟；这是历史测量，不是构建时长保证。内存不足先降低 `--jobs`。

## 2. 分别构建仿真器和软件

### RTL 仿真器

```bash
python3 experiments/multicore/tools/ncoral.py build-sim --jobs 2 --timeout 1800
```

构建链路：

```text
Cluster.scala + die/Die.scala + 上游 Coral RTL + PicoRV32
    → Chisel/firtool 生成 SystemVerilog
    → Verilator 生成 C++ 仿真模型
    → G++ 链接 die/smoke_die.cc 测试台
    → bazel-bin/experiments/multicore/smoke_die
```

Bazel 目标是 `//experiments/multicore:smoke_die`，生成的顶层模块是 `CoralDie`。脚本打印完整 Bazel 命令并保存命令、日志、仿真器 SHA256 和集成源码哈希。**这个命令不编译固件，也不运行仿真。** 修改 Scala、Verilog、C++ 测试台或其头文件后重新执行，Bazel 按依赖增量构建。

### Coral kernel 与控制 CPU 固件

```bash
python3 experiments/multicore/tools/ncoral.py build-code --timeout 30
```

| 对象 | 源码 | 编译目标 | 产物前缀 |
|---|---|---|---|
| Coral 向量加法 | `device/start.S`、`device/add.c` | `riscv32-none-elf`，`rv32im_zicsr_zve32x_zvl128b`，`ilp32` | `coral` |
| Coral GEMM/卷积 | `device/start.S`、`device/complex.c` | 同上 | `complex` |
| 控制 CPU | `die/firmware/start.S`、`runtime.c`、`main.c`、`complex_demo.c` | `riscv32-none-elf`，`rv32im`，`ilp32` | `controller` |

这是两类目标程序，控制 CPU 不执行 Coral 的 RVV 指令。共用一套 Clang/LLD，通过不同的 `-march` 分别编译。当前没有 `-mcpu=coral`，没有启用 VME/Zvt 或任何新增私有指令。

kernel 的编译流程：

```text
add.c / complex.c
    ├─ clang -S -emit-llvm → coral.ll / complex.ll
    ├─ clang -S           → coral.s  / complex.s
    └─ clang + start.S + device/link.ld + LLD → kernel ELF
           → llvm-objdump：验证实际 RVV 指令
           → llvm-objcopy：生成 ITCM binary
           → Python：生成 coral_image.h / complex_image.h
控制核 C + start.S + 生成的 image.h + 控制核 link.ld
    → controller.elf → controller.bin
```

共用主要参数是 `-O2 -ffreestanding -nostdlib -mabi=ilp32`，关闭链接松弛、小数据区和自动向量化。RVV 使用显式 `riscv_vector.h` intrinsic，因此仍生成向量指令。完整参数以固件 `report.json` 中的 `commands` 为准，脚本没有隐藏额外的交叉 GCC 工具链。

设备代码当前只支持不超过 8 KiB 的 ITCM 镜像；参数位于每核私有 DTCM 的 `0x10000`，栈顶 `0x18000`。设备启动代码没有通用 `.data` 装载和 `.bss` 清零流程，新增全局数据之前先完善 ELF 分段加载。控制固件链接区为 48 KiB，硬件启动 RAM 为 64 KiB，剩余空间用于栈及启动配置。

修改 kernel 后必须重建 **整个 build-code**：kernel binary 嵌入控制 CPU 镜像中，只替换 `complex.bin` 不会改变实际执行内容。修改控制核 runtime 同样只需重建 build-code。复用固件时会校验源码集合、编译脚本和镜像哈希，源码或编译配方变化会要求重新构建。

## 3. 运行场景与直接调用仿真器

```bash
python3 experiments/multicore/tools/ncoral.py run --elements 37 --rounds 2
python3 experiments/multicore/tools/ncoral.py demo --rows 7 --stall-period 3 --run-timeout 420
python3 experiments/multicore/tools/ncoral.py run --help
python3 experiments/multicore/tools/ncoral.py demo --help
```

`run --rounds 2` 是依赖链 `C=A+B → D=C+B`。`demo` 的 GEMM 与卷积互不依赖，分别使用 0/1 核和 2/3 核；串行先等待 GEMM 结束再启动卷积，并行连续提交两组，然后统一等待。多核计划由控制核上的 C runtime 明确编写。

默认产物目录为 `experiments/multicore/tools/die/logs/`。也可直接执行仿真器，以便使用调试器或自定义测试脚本：

```bash
FW=experiments/multicore/tools/die/logs/firmware/controller.bin
timeout --signal=TERM --kill-after=10s 120s \
  bazel-bin/experiments/multicore/smoke_die \
  --boot "$FW" --elements 37 --rounds 2 --stall-period 3 --max-cycles 2000000

# scenario=1 串行；scenario=2 并行。
timeout --signal=TERM --kill-after=10s 420s \
  bazel-bin/experiments/multicore/smoke_die \
  --boot "$FW" --scenario 2 --rows 7 --stall-period 3 \
  --trace-json /tmp/ncoral-parallel.json --max-cycles 2000000
```

直接调用不包含 Python 的固件哈希校验和串并行数组交叉检查；优先用统一入口。若指定了 `NCORAL_WORK_DIR`，上述 FW 路径也要改到该目录下的 `die/logs/firmware/controller.bin`。

## 4. 日志、截止时间与缓存

| 默认路径（相对于 `experiments/multicore/tools/`） | 内容 |
|---|---|
| `die/logs/doctor.json` | 本地环境检查 |
| `die/logs/simulator/build.log`、`report.json` | 独立 RTL 构建日志和摘要 |
| `die/logs/firmware/` | 三套 ELF/bin、kernel IR/汇编、链接 map、反汇编、image.h、编译命令与哈希 |
| `die/logs/report.json`、`run.log` | 普通场景结果 |
| `die/logs/complex/rows7_stall3/` | 两种调度的日志、事件/输出 JSON、交叉检查报告 |
| `die/logs/regression/report.json` | 19 项回归及每项日志位置 |
| `.tools/` | 固定 Bazel、下载和构建缓存 |

这些生成文件不提交 Git。可以在执行所有命令前设置 `export NCORAL_WORK_DIR=/absolute/artifact-directory`，将缓存与日志统一移动到仓库之外。**同一次构建、代码编译和运行应保持相同环境变量。** 不设置时使用仓库内默认目录；旧的本机缓存目录可选 `/home/shaokai/Desktop/work/coral`，这不是源码要求的固定位置。

| 阶段 | 默认最大等待时间 |
|---|---|
| `build-sim` 的 Bazel 阶段 | 1800 秒 |
| `build-code` 每个编译/反汇编子命令 | 30 秒 |
| `run` | 60 秒，可用 `--run-timeout` 调整 |
| `demo` 每种调度 | 420 秒，串并行共两个阶段 |
| `test` 每个普通/复杂用例 | 120 / 420 秒 |

每个受监控进程先打印 UTC 绝对截止时间，再等待和核对结果。下载准备有独立网络超时，未包含在 Bazel 阶段预算内；进程组超时终止最多再等待 10 秒。另有仿真周期上限和固件等待上限，三者不能互相替代。超时不是 PASS，预期错误用例必须命中特定错误才算通过。

## 5. 修改后的最短开发循环

```bash
# 改 kernel 或 runtime：重编软件，复用 RTL。
python3 experiments/multicore/tools/ncoral.py build-code
python3 experiments/multicore/tools/ncoral.py demo

# 改硬件或 C++ 测试台：重建 RTL，继续使用未改变的软件。
python3 experiments/multicore/tools/ncoral.py build-sim --jobs 2
python3 experiments/multicore/tools/ncoral.py test --jobs 3
```

找不到镜像：先执行 `build-code`；找不到仿真器：执行 `build-sim`。编译失败先看相应 `report.json` 的 `error` 和 `phases`，然后打开失败阶段日志。超时先分清构建、宿主仿真、芯片等待还是周期截止，不能只增加所有超时掩盖错误。

系统的实际范围和后续任务见 [架构与待办](model_status.md)，历史 RVV 运行证据见 [RVV 验证](rvv_execution.md)。
