# Coral 单核 ELF 仿真工具

本目录属于 `coralnpu-sim` 分支。它把功能仿真入口、RTL 仿真反馈、裸机运行库、示例和回归测试放在同一个 Git 仓库，生成文件统一放在仓库根目录的 `out/coral-sim/`。目录仍叫 `coralnpu-main` 也可以，Git 分支名与目录名无关。

## 1. 构建

仓库根目录的一键入口是 `build-sim.sh`，无需提前设置 PATH 或进入工具子目录：

```bash
# 默认构建 MPACT 功能仿真器，并从源码编译测试 ELF、运行回归。
./build-sim.sh --test

# 同时构建功能仿真器和 Verilator RTL 仿真器，并测试二者。
./build-sim.sh --target all --test

# 仅构建 RTL。
./build-sim.sh --target rtl
```

依赖：Linux、Python 3.9+（含 zoneinfo/tzdata）、GCC/G++、make、Clang/LLD/LLVM tools 17 或更新版本、Java 11+、Bazel（版本见根目录 `.bazelversion`，当前为 9.1.0）或 Bazelisk。RTL 还需要仓库构建环境中的 autoconf、bison/flex、libelf、liblz4、libmpfr 等依赖；完整基础环境参考 `../coralnpu.dockerfile`。首次构建需要下载仓库固定版本的依赖，耗时取决于机器与网络。无需提前编译 LLVM 项目或安装 RISC-V GCC。

Bazel 查找顺序：`--bazel` / `BAZEL` → PATH 中的 bazelisk/bazel → 仓库同级 `.tools/bazel-<版本>-linux-x86_64`。Clang 默认 `clang-17`；可以传入 `--clang clang-19`。LLD、llvm-ar、llvm-objcopy 优先寻找与所选 Clang 同版本的工具，其次寻找 PATH 中的无版本名称和 17 版本。Java 优先使用 `--java` 或 PATH，然后查找已有缓存中的 JDK；必要时由 Bazel 的 `java-home` 查询定位。

构建脚本会复用当前工作区 `bazel-out` 对应的输出缓存；若仓库同级存在 `.tools/bazel-cache`、`distfiles`、`system-zlib`，也会复用。新检出没有这些目录时使用 Bazel 默认缓存和仓库原有依赖定义。`system-zlib` 是本机旧构建环境的可选依赖替代，并非提交的第三方源码；本机验证结果应结合生成的构建命令查看。

```bash
./build-sim.sh --test --jobs 4 --timeout 1800
./build-sim.sh --bazel /path/to/bazel --clang clang-17 \
    --java /path/to/jdk/bin/java --output-base /path/to/cache
```

`--timeout` 是每次 Bazel 构建的墙钟上限，默认 1800 秒；每组回归上限 300 秒。每步开始前打印具体截止时间；超时停止子进程并返回失败。完整命令、开始/截止/结束时间、退出码及日志在 `out/coral-sim/logs/`。构建成功才原子替换已安装的二进制。

## 2. 编译并执行自己的程序

```bash
# 编译示例；也可换为自己的 /path/to/operator.c。
python3 utils/coral_sim/coral_main.py compile utils/coral_sim/examples/hello.c

# ELF 中的程序输出到 stdout，指令计数和状态输出到 stderr。
out/coral-sim/bin/coral-sim out/coral-sim/elf/hello/hello.elf

# 加入 PATH 后，即可使用 coral-sim xxx.elf。
export PATH="$PWD/out/coral-sim/bin:$PATH"
coral-sim out/coral-sim/elf/hello/hello.elf
```

程序本身输出：

```text
Hello, world!
7 + 5 = 12
hex = 0x0000000c
```

stderr 包含 `Deadline`、`Instructions: <实际动态执行计数>`、`Exit status`。不会自动把程序结果替换成固定的 `Program result: PASS`。重定向 `>result.txt` 得到纯程序输出，包括原始 NUL 字节。

```bash
coral-sim operator.elf --timeout 30 --max-instructions 10000000 --verbose
coral-sim operator.elf --cpu coral-v2

# 需要先构建 RTL；同一个 hello ELF 可交叉验证。
coral-sim out/coral-sim/elf/hello/hello.elf --engine rtl
out/coral-sim/bin/coral-rtl-sim out/coral-sim/elf/hello/hello.elf
```

内置 C 编译助手使用 `rv32imf_zicsr_zve32x_zvl128b` / `ilp32`、Clang + LLD，自带启动代码和链接脚本。小程序放入 8 KiB ITCM；完整 printf 与软件 double 辅助例程超过 ITCM 时，自动将大部分代码放到 `0x20000000` 外部 AXI RAM，启动段与部分热点仍在 ITCM。数据和栈放入 32 KiB DTCM，预留 4 KiB 栈。`elf/<名称>/layout.json` 记录采用的布局。

此编译助手提供基础裸机 stdio，而非完整 libc：`printf`/`snprintf` 等来自固定提交的 mpaland/printf，RV32 软件双精度和整数辅助例程来自 LLVM compiler-rt，均附带上游版本、许可证和 SHA256 清单。打印浮点数需要这些格式化/软件 double 支持，不代表必须让每个程序依赖它们。使用自己的 ELF 时无需链接本目录运行库，只须满足下面的 ISA 与 I/O 约定。

## 3. 支持范围与运行约定

| 项目 | 默认功能仿真 `iss` | RTL `rtl` |
| --- | --- | --- |
| 模型 | 仓库固定版本的官方 Coral MPACT 指令语义 | RVV core_mini_axi 的实际 Verilator/SystemC 模型 |
| ELF | ELF32，小端，RISC-V 裸机；入口与地址来自 ELF | ELF32，小端；需要匹配硬件内存图 |
| 已回归路径 | RV32 标量、F、CSR、RVV，默认 Coral V2 / VLEN=128 | 单核标量、F、RVV / VLEN=128 |
| UART 输出 | 观察 `0x54000000` 存储并转发原始字节 | 真正 AXI 访问驱动 UART 回调，支持字节使能 |
| ECALL | read=63，write=64，exit=93/94；仅基本 stdin/stdout/stderr | 不提供宿主系统调用代理 |
| 半主机 | 标准 RISC-V marker；WRITEC、WRITE0、EXIT、EXIT_EXTENDED | 使用 UART/裸机结束约定 |
| 正常结束 | `mpause`、exit ECALL、半主机 exit | `mpause` |
| 指令数 | MPACT 动态指令计数，含模型评估的终止/异常指令 | 硬件 `minstret`，停止后经 AXI 读回 |
| 周期 | 不报告硬件周期或芯片性能 | 硬件 `mcycle`；另存整个仿真的时间 |
| 内存 | 稀疏 32 位平坦地址空间，无 TCM 容量约束 | ITCM 8 KiB、DTCM 32 KiB 及外部 AXI RAM |

功能模型默认栈顶 `0x7fffe000`，可用 `--stack-top` 改写；ELF 的启动代码也可以自行设栈。ECALL 使用 a7 编号、a0–a2 参数；write 的 fd 仅允许 1/2，read 仅允许 0。实现的是上述小集合，不提供文件系统、动态链接、进程或完整 Linux ABI。

Coral 模式拒绝 RV64 和带压缩 C 标志的 ELF；未知/非法指令、异常或普通 ebreak 返回失败，指令数或墙钟限制返回 124。示例启动代码把 `main != 0` 转成失败 ebreak，因此返回宿主状态 1；自行使用 exit ECALL 可返回指定的 0–255 状态。

可选 `--abi linux` 调用已安装的 `qemu-riscv32[-static]` / `qemu-riscv64[-static]`，支持 `--sysroot` 和 `--` 后的参数。该模式不执行 Coral 自定义指令；没有计数插件时明确报告指令数不可用，不伪造计数。

`--cpu coral-m3|coral-m4` 仅选择上游 MPACT 对应架构，不代表此分支已验证这些架构的所有矩阵指令。本次回归覆盖标量/F/RVV 和 I/O。后续自定义 Zvt/VME 编码须同时实现 LLVM 编码/选择与 MPACT 解码/语义，并添加独立期望值测试；当前 RTL 目标没有新增矩阵执行单元。

## 4. 源码与结果目录

```text
experiments/functional/          # coral_iss.cc、Bazel target：功能模型宿主
utils/coral_sim/
  build.sh, build.py             # 根目录 build-sim.sh 调用的构建实现
  coral-sim, functional.py       # ELF 功能仿真 / 可选 Linux QEMU 分派
  coral-rtl-sim, coral_main.py   # RTL 运行与 C 编译助手
  feedback.py                   # RTL 内存读回、数组检查、Markdown 报告
  examples/                     # C、启动汇编、TCM/AXI 链接脚本
  functional-examples/           # 不依赖本运行库的 ECALL/半主机汇编
  runtime/                      # 裸机 stdio 适配
  third_party/                  # 固定版本 printf 和 compiler-rt 子集
  test_functional.py            # 自行编译测试输入，再运行功能回归
  test_console.py               # RTL 字节输出、strip ELF、实时输出测试
out/coral-sim/                   # 全部忽略提交
  bin/                          # 可执行文件与命令入口
  host-tools/                   # 仅构建使用的 Clang/Java 工具链接
  build/runtime/                # 目标运行库缓存
  elf/                          # ELF、map、布局和编译记录
  logs/                         # 构建、测试日志及命令 JSON
  functional-validation/        # 功能回归逐例 stdout/stderr 和 verification.json
  console-validation/           # RTL 控制台回归结果
  functional-runs/               # 每次功能执行的 model.json 与 run.json
  runs/                         # 每次 RTL 执行的原始 console、日志、SRAM、报告
```

用 `CORAL_SIM_OUT=/absolute/path` 可指定输出目录，构建、编译、运行都应使用相同环境变量。生成的 `bin/coral-sim` 是指向此检出源码入口的链接，移动/清理检出后应在新位置重新运行构建脚本。

## 5. 回归内容

`build.sh --test` 会从源码构建 fixture，检查 hello 原始输出、浮点 printf/snprintf 截断、二进制 NUL、RVV 自校验、独立 ECALL ELF、stdin 回显、标准半主机、strip ELF、非零返回、非法指令、指令上限、RV64 拒绝及无效 ELF；若系统有 QEMU，再测试 Linux RV32/RV64 运行。

`--target all --test` 追加 RTL 的 RVV 独立读回校验、非零返回、非法指令、错误结果检测、墙钟超时，以及六项控制台测试。测试失败时构建入口返回非零；诊断信息和逐项结果留在输出目录。
