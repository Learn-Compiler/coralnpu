# DMA、事件队列与四核 Zvt 接入

开发基线为 `feat/ncoral-model` 的 `180b1ca6`，实现分支为 `feat/ncoral-dma-events-zvt`。

## 实现边界

- DMA 是 `die/Dma.scala` 中独立、可综合的 AXI 主设备，能够在共享 SRAM 与四核 ITCM/DTCM 地址窗间搬运。控制 CPU 只写描述符，不执行数据复制循环。
- 事件和任务队列运行在真实 PicoRV32 控制 CPU 上，代码为 `die/firmware/queue.[ch]`。它不是新加的硬件命令处理器。
- 四个 Coral 核通过 `enableVme` 及 `ZVT_ON` 启用仓库已有 Zvt RTL；新增 `smoke_die_vme` 目标保留原 RVV 目标。
- 矩阵用例使用补丁 LLVM 汇编实际助记符，实现有正负输入的 16×16×4 INT8 矩阵乘法，输出 INT32。仿真测试台以独立 INT64 计算逐元素检查 4 个 16×16 输出，并检查每核四次矩阵指令派发。FP32/BF16 数值结果不属于这个新增用例的验证范围。

## 总线与 DMA

每个核的 host 口在 CPU 和 DMA 间仲裁；共享 SRAM 在核簇、CPU 和 DMA 三个 AXI 主设备间仲裁。读事务到 RLAST、写事务到 B 响应期间锁定所属主设备，因此不要求不同主设备使用不同 AXI ID。

DMA CSR 基址为 `0x50000100`，只接受对齐的 32 位全字写入：

| 偏移 | 名称 | 语义 |
| --- | --- | --- |
| 0x00 | CTRL | bit0 START，bit1 清除完成/错误（仅空闲时） |
| 0x04 | STATUS | bit0 busy，bit1 done，bit2 error |
| 0x08 / 0x0c | SRC / DST | 完整的系统设备地址 |
| 0x10 | BYTES | 非零且为 4 的倍数 |
| 0x14 / 0x18 | TAG / DONE_TAG | 提交标识与执行标识 |
| 0x1c | ERROR | 1 配置/重叠，2 地址范围，3 AXI 读，4 AXI 写 |
| 0x20 | PROGRESS | 已收到成功写响应的字节数 |
| 0x24 | COMPLETED | 成功搬运数量 |
| 0x28 | REJECTED | busy 期间被拒绝的 START 数量 |

源、目标和长度需 4 字节对齐；两端均 16B 对齐且剩余足够时使用 128 位单 beat，否则使用 32 位单 beat处理错位与尾部。每次只有一个 outstanding beat，不声称实现 burst/ping-pong 的峰值带宽优化。完成仅在最终写响应成功后发布。

允许共享 SRAM `[0x20000000, 0x20003ff0)` 和每核 host 地址窗中的 ITCM/DTCM。禁止触及系统 CSR、核控制 CSR、日志端点、越界和地址回绕；拒绝重叠拷贝。修改配置寄存器不会改变已启动描述符。没有取消正在执行的总线事务的接口。

## 队列和事件契约

队列固定 32 项、事件固定 64 项，不依赖堆内存。任务支持 `NC_COPY`、`NC_KERNEL`，最多两个前驱事件和一个完成事件，带核掩码、kernel ID、逻辑 program ID/grid 与参数。

- 在保持提交记录 FIFO 回收的同时，扫描所有任务，允许独立的已就绪任务越过等待前驱的任务启动。
- 单个 DMA 通道串行使用；同一核上的 DMA 和 kernel 互斥，不同核间可并行调度。
- 共享缓冲区的读写依赖由提交者显式提供事件；队列不自动推断所有地址别名。
- COPY 的核资源从源/目标地址推导，调用者不能漏报私有内存资源。
- 完成事件仅在 backend 完成确认后变为 SUCCESS。失败变为 ERROR，依赖任务传播 NC_DEPENDENCY，不执行也不发布成功事件。
- token 带 24 位 generation 和 slot，释放后旧 token 失效；generation 用尽时明确失败，不回绕别名。
- 事件有引用计数，仍被任务引用或处于 PENDING 时不能释放。由调用者在消费完之后释放；没有自动垃圾回收。
- 没有运行任务、没有可启动任务而还有等待项时报告依赖死锁。运行中的硬件无响应由外层周期预算处理。
- 当前控制 CPU 单线程驱动队列；该 API 不提供多线程并发提交和硬件中断抢占。

新增流水线用例为每个核提交 `DMA A → DMA B → kernel → DMA C`，kernel 等待两份输入完成，输出搬运等待 kernel 完成。零元素分片只启动空 kernel，避免产生非法零长度 DMA。

## 构建和验证

新 LLVM 安装路径仅作为本机示例，可替换为带同一补丁的工具链：

```bash
cd /home/compiler-1/work/coral/coralnpu
export PATH=/home/compiler-1/work/coral/coralnpu-compiler/out/llvm-zvt/install/bin:$PATH

python3 experiments/multicore/tools/ncoral.py build-code --zvt --clang "$(command -v clang)" --lld "$(command -v ld.lld)"
python3 experiments/multicore/tools/ncoral.py build-sim --vme --jobs 12
python3 experiments/multicore/tools/ncoral.py pipeline --zvt --test --jobs 2
```

`NCORAL_BAZEL_OUTPUT_USER_ROOT=default` 可选择复用用户默认 Bazel 缓存；未设置时保持原有工具专用缓存。`NCORAL_WORK_DIR` 继续控制报告目录。

`pipeline --test` 先在 ASan/UBSan 下检查队列依赖、资源互斥、并发、回绕、过期 token、错误传播、环依赖和容量；然后执行 RTL 的单元素、空分片、非整块尾部、背压、无效地址和重叠拒绝用例。添加 `--zvt` 后再执行两个带独立数值参考的矩阵用例。每项独立设置宿主截止时间和仿真周期预算。运行结果写入 `tools/die/logs/pipeline/report.json`，不能将构建成功当成数值通过。

## 本轮实测（2026-11-13）

- 新增流水线 RTL 回归：9/9 PASS，包含单元素、空分片、尾部、背压、无效 DMA 地址、重叠拒绝、busy 时拒绝与活动描述符快照、两个 Zvt 数值用例。
- 原有 RVV 多核回归：23/23 PASS，包括加法、多次依赖调用、GEMM/卷积串并行、异常、超时和退休指令规模检查。
- 队列宿主检查在 AddressSanitizer / UndefinedBehaviorSanitizer 下通过。
- 每个新增 guest 用例都检查控制 CPU 的 SB/SH、非对齐偏移 memcpy 和部分 memset。修复了混合全字/字节写端口导致生成 RTL 丢失字节使能的问题，启动 RAM 改为四个显式字节 bank。
- Zvt 两个用例均为四核 16×16×4 有符号 INT8 矩阵乘法：逐项核对 1024 个 INT32 结果，每核派发 4 条矩阵指令，12 次 DMA 完成，checksum 为 177。正常/背压用例的控制执行周期分别为 752973 / 754533；这些是当前功能模型和带调度开销的结果，不用于宣称芯片吞吐。
- 最终新增回归 simulator SHA256：`aca9e595d8eb5a68ed588b52cc9598c04de8c20b375cf8d8e0c1269c83b54e45`。
- 最终新增回归 controller SHA256：`c6f5e658a7b648a6484d25dbe0bd0e10d2bc5b4c0ab31272ee9d44d19d1da450`。

完整报告位于 `tools/die/logs/pipeline/report.json`、`tools/die/logs/regression/report.json`、`tools/die/logs/simulator/report.json` 和 `tools/die/logs/firmware/report.json`。原有回归运行后，仅进一步修改了新增 busy 注入用例和诊断输出；两组报告分别保留各自的二进制哈希，不混用运行来源。

当前限制：DMA 要求 4B 对齐，不支持重叠 memmove；队列容量固定、最多两个直接前驱；共享缓冲别名依赖需提交者提供；无抢占/中断；矩阵数值验证只覆盖上述 INT8→INT32 路径，未验证 BF16/FP32。Zvt 阵列来自已有 RTL，本轮没有做物理面积、频率或峰值带宽签核。

## 先做单核后端是否影响以后多核

可以先做单核。把以下几个契约先固定，后续主要扩展 runtime 调度和 compiler mapping：

1. TTIR 的 program ID/grid 是逻辑索引，不能用物理 core ID 替代。单核可以顺序执行多个 program，多核将它们分配给不同核。
2. kernel 接收设备地址，不接收宿主指针。`device/task_abi.h` 给出同一份 v2 kernel 参数布局；单核和多核不需要两套函数签名。
3. 从一开始区分共享内存与每核私有内存，即使只有一个核。单核正确不证明跨核私有地址可以互访。
4. kernel 负责核内计算；控制 CPU/runtime 负责镜像加载、参数、启动、完成和跨任务依赖。不要把这些 MMIO 调度动作固化到每个 Triton kernel。
5. Zvt/RVV/DMA 作为 target capability；编译器只为可用能力生成操作。单核阶段可以使用 RVV，之后加入 Zvt lowering，不依赖核数。
6. 正常 Triton program 间不隐式添加 barrier。跨 program 协作需要明确额外语义，单纯增加核数不会自动获得它。

目前 v2 kernel 用例演示逻辑 ID 与核 ID 分离，尚未实现 TTIR → Coral 方言或 Triton backend；它们仍由用户的单核编译器学习路线完成。
