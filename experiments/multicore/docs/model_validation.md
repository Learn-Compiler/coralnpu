# 建模分支验证

日期：2026-10-18，Asia/Shanghai；日志内部使用 UTC。分支：`feat/ncoral-model`，基础提交：`ac808307`。机器可读摘要见 [model_validation.json](model_validation.json)。

本轮验证新增的统一入口、独立仿真器构建入口、软件编译产物及固件复用校验。硬件和算子语义未改变，使用真实 PicoRV32 控制 CPU 与四个 Coral RVV 核。

| 检查 | 实际结果 |
|---|---|
| `ncoral.py doctor` | 所有本地环境检查通过；工具存在检查，不代替编译 |
| `ncoral.py build-code` | 两个 RVV kernel 与 RV32IM 控制固件全部构建成功；IR、汇编、ELF/bin、map 与反汇编存在 |
| `ncoral.py build-sim --jobs 2 --timeout 1800` | Bazel 构建成功；复用已有缓存，不是全新环境冷构建 |
| `ncoral.py build --jobs 2` | 代码编译和仿真器构建的组合入口成功 |
| `ncoral.py run --elements 37 --rounds 2 --run-timeout 120` | 真实 CPU 控制四核，两次依赖加法通过，checksum=11507，8 次核启动 |
| `ncoral.py demo --run-timeout 420` | 串行/并行均通过，GEMM 84 个与卷积 224 个输出全部正确，完整输出数组相同 |
| `ncoral.py test --jobs 3` | 19/19 通过，包括预期失败用例的特定错误检查 |
| 固件源码复用校验 | 当前源码校验通过；临时替换源码根目录模拟源码变化，确认运行前拒绝旧镜像 |
| Python 与文档 | Python compileall、命令帮助和本次维护的 Markdown 相对链接检查通过 |

本轮默认 7 行演示，两种调度均得到每核 RVV MAC 派发次数 `[288, 216, 576, 432]`、checksum=4436。串行 GEMM 最后 END 在周期 201617，卷积首个 START 在 202067；并行卷积首个 START 在 194678，早于 GEMM 最后 END 204308。并行期间 GEMM 7 行、卷积 4 行在另一算子仍活动时完成。

每个受监控编译/仿真进程启动前均输出截止时间；所有最终结果来自正常完成的进程。预期错误用例通过不代表该用例返回 0，而是检测到预先指定的错误。宿主并发运行多个仿真实例，因此本轮宿主耗时不用于硬件性能比较。

默认目录下保留 `die/logs/firmware/`、`die/logs/complex/rows7_stall3/`、`die/logs/regression/` 和普通 `report.json`。RTL 构建和组合构建使用 `NCORAL_WORK_DIR=/home/shaokai/Desktop/work/coral` 复用既有工具缓存，对应完整构建日志在该目录的 `die/logs/simulator/`。以上日志和二进制被 Git 忽略，版本化摘要记录源码与二进制哈希。

本轮没有验证新机器首次下载全部依赖、不同 LLVM 版本、私有指令、Zvt 矩阵单元或完整语言模型。运行教程见 [build_and_run.md](build_and_run.md)，具体未完成事项见 [model_status.md](model_status.md)。
