# 本地网页：芯片架构与算子工作台

在 `coralnpu` 仓库根目录运行：

```bash
python3 experiments/multicore/tools/ncoral.py ui --open
```

- **架构专页**：<http://127.0.0.1:8765/architecture>
- **算子工作台**：<http://127.0.0.1:8765/>

使用 Python 标准库启动本地 HTTP 服务，无需 npm、前端框架或联网加载资源。架构图与模块说明在没有仿真器、固件时也能查看。默认只监听 `127.0.0.1`，默认运行两小时；终端先打印服务截止时间，到期自动停止。端口占用时用 `--port 8766`。Ctrl+C 停止服务，也取消当前仿真。

## 架构图读法

图对应当前 `die/Die.scala`、`Cluster.scala` 和 `die/firmware/platform.h`：

1. 芯片外的 C++ 测试台装入 CPU 启动镜像、配置输入，并独立核对输出。
2. PicoRV32 RV32IM 控制 CPU 从 **64 KiB RAM** 执行控制固件和 C runtime。
3. `DieControlFabric` 译码 native bus，提供四个 Coral host 端口和一个共享 SRAM 端口。
4. 四个独立 Coral 核各有 **8 KiB ITCM、32 KiB 私有 DTCM**；核内执行标量和 RVV，VLEN=128。
5. 四核 AXI master 经 **4→1 轮转仲裁**，再与 CPU 的共享端口合并，访问 **16 KiB SRAM**。
6. CPU 轮询系统 WFI / fault 状态；共享区保留计数器与 MMIO 日志端点。

蓝线显示控制、镜像加载和 MMIO 路径；绿线显示核的共享数据访问路径；灰线显示启动和状态关系。箭头主要表示请求发起方向，AXI 的响应也沿相同连接返回。点击任一模块可查看职责、地址和对应源码；可用 Tab / Enter 键操作。顶部按钮可单独突出控制或数据连接，工作台内可展开架构图。

网页将 CPU 和四个 Coral 的内部功能模块全部展开，每个 Coral 各有独立绘制的 Fetch、LSU、Decode/Dispatch、Scalar GPR、ALU/M/FPU、Retire/CSR、RVV Vector RF / ALU/MAC，以及 VME/Zvt 矩阵预览。CPU 展示 PC/取指、译码、GPR、ALU/分支、PCPI 乘除法、多周期控制和 native 访存。PicoRV32 的这些框是功能拆分，不表示五级流水线。

矩阵预览参考仓库已有 `hdl/verilog/rvv/design/Zvt/zvt.sv` 中的 `zvt_ctrl`、`zvt_mt`、`zvt_pe_array`：图中画出控制器、Tile RF 与 PE/MAC，并展示 LSU → Tile、Tile → PE、累加写回与存储路径。**当前四核 die 未启用 VME/Zvt**，紫色虚线标识接入预览；打开矩阵显示不会修改仿真器、启用硬件指令或生成矩阵性能测量。PE 小格仅是阵列符号，不代表实际阵列规模。

工具栏可聚焦 CPU 或任一 Coral，使用适应宽度、100%、125% 缩放；小屏可在图内横向滚动。内部框是功能抽象，省略逐引脚和门级细节；图不代表物理布局或布局布线结果，未接入 HBM、3D 或多卡链路。

### 数据流动画

点击“播放数据流”，或用“下一步”、进度滑条逐步查看；可以暂停、重置或调整速度。高亮显示当前工作的模块，移动标记沿对应连接传播。

| 教学场景 | 展示内容 |
|---|---|
| 矩阵 GEMM | CPU 下发 → 四核取指 → LSU 加载 A/B Tile → VME 控制 / PE 乘加 → Tile 累加 → SRAM 写回 |
| RVV ADD | CPU 下发 → 四核 RVV 取操作数、向量计算、写回 |
| 两组并行 | 核 0/1 的矩阵 PE 与核 2/3 的 RVV 同时高亮计算，并显示共享存储路径 |
| 两组串行 | 第一组完成与 CPU 依赖屏障之后，才进入第二组的向量计算 |

这些是**教学动画，不是 RTL 实时 trace**。动画节奏不表示芯片周期、仲裁带宽或算子吞吐，也不会启动仿真。矩阵+RVV 两组场景表示扩展目标；当前真实 RTL 的双算子入口仍是 GEMM+Conv，二者都用 RVV 实现。真实周期和退休指令请在工作台启动算子后查看验证通过的报告。

## 通过网页运行算子

首次使用先构建一次：

```bash
python3 experiments/multicore/tools/ncoral.py build
```

在工作台选择已编译算子，配置参数并点击“启动仿真”：

| 选项 | 执行方式 |
|---|---|
| ADD | 四核分片；1 或 2 轮，第二轮存在依赖 |
| GEMM + Bias + ReLU | 四核按输出行分片 |
| Conv3×3 + Bias + ReLU | 四核按输出行分片 |
| GEMM + Conv | 固定核 0/1 执行 GEMM，核 2/3 执行 Conv；选择串行或并行 |

参数范围沿用当前小尺寸验证模型：ADD 1–128 元素，GEMM/Conv 1–8 输出行。频率是周期到时间的**假设换算值**，并未校准芯片频率。总线停顿用于背压注入。

网页和 `ncoral.py op run` 共用执行函数，均校验固件 bundle、运行真实 RTL、检查 CPU 控制的数值正确性，再读取硬件 `mcycle/minstret`。网页不编译代码，不修改 LLVM 或 RTL。

- 同时只运行一个仿真任务。每次开始前确定绝对截止时间，任务上限为 1–3600 秒。
- 点击“取消”或停止服务会终止仿真进程组并回收子进程；超时同样终止。清理最多额外等待约 10 秒。
- 日志显示仿真器已经写出的内容（最后 64 KiB），刷新间隔约 1 秒。输出缓冲可能导致日志延后出现。
- 成功后显示每核时间线、周期、退休指令与数值校验结果。时间线播放是对完成记录的回放。
- 芯片执行窗口为最早 START 至最晚 END，四核指令为各核退休计数之和；**宿主仿真耗时单独显示**。
- 核分配示意表示表单配置，精确核起止与重叠关系在完成报告中读取。
- 取消、超时和校验失败均保留日志，不生成成功报告。

每次运行写入独立目录，默认位于 `experiments/multicore/tools/die/logs/operators/`。可通过 `--results /path/to/results` 修改；固件可用 `--firmware /path/to/bundle/controller.bin` 指定，仍要求完整且校验通过的 bundle。`NCORAL_WORK_DIR` 的行为与其他命令一致。

运行目录保存 `job.json`、`execution.json`、`run.log`；成功时另有 `counters.json`、`profile.json`、`report.html`。最近 100 个运行目录可从左侧记录切换，重启仍可查看。服务意外中断的历史任务显示失败，不能从未验证的记录生成成功报告。原有 CLI 成功报告也可查看。

本地服务仅接受固定算子和有界数值参数；页面请求不能传入命令或文件路径。每个服务会话使用随机提交令牌并检查 Host / Origin；报告下载限定在结果目录内的已知文件。服务适用于本机开发学习，不作为多用户服务器部署。

## 验证

```bash
python3 -m unittest discover -s experiments/multicore/tools/die -p 'test_*.py' -v
```

测试覆盖原有性能报告验证、参数边界、请求来源校验、任务互斥、取消、超时、文件访问范围和中断历史恢复。网页真实 RTL 运行验证记录见 `dashboard_verification.md`。
