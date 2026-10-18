# 网页架构图与工作台验证

日期：2026-10-18。分支：`feat/ncoral-model`。结构化记录与源码、二进制 SHA256 见 [dashboard_verification.json](dashboard_verification.json)。

## 功能与浏览器检查

- 独立架构入口 `/architecture` 与运行工作台 `/` 均可打开。
- 页面生成 84 个功能框，CPU 和四个 Coral 的内部结构均可查看；四核各有独立矩阵控制、Tile RF 与 PE/MAC 预览。
- 矩阵+RVV 并行阶段高亮 `c0/c1 Tile + PE` 与 `c2/c3 Vector RF + ALU/MAC`。
- 串行流程在第二组执行前经过 `CPU native 端口 / runtime / 系统状态` 依赖屏障。
- 播放/暂停、单步、进度滑条、速度、模块点击、控制/数据路径筛选、矩阵显示开关、CPU/单核聚焦均已通过浏览器操作检查。
- 桌面和窄屏布局已检查：全图适应模式可容纳所有四核；放大模式在图内滚动；单核聚焦提供可读的内部结构。检查未发现模块文字溢出。
- 工作台仍能显示真实 Conv 报告，7,245 芯片周期、7,821 条 Coral 退休指令；切换双算子串行可正确显示 0/1 GEMM、2/3 Conv 配置。
- 浏览器未记录 JavaScript 错误。截图位于忽略的 `tools/die/logs/dashboard/architecture_view.jpg`。

矩阵部分是参考仓库已有 VME/Zvt 实现的**架构接入预览**；动画是教学示意。这里未启用矩阵 RTL、修改指令集、执行矩阵指令或产生矩阵性能测量。

## 真实 RTL 运行

| 网页运行 | 输入 | 校验 | 芯片测量 | 结果 |
|---|---|---|---|---|
| ADD | 37 元素，2 轮，4 核，超时 180 秒 | CPU 控制执行、逐元素数值检查 | 每轮 306 周期、176 条退休指令 | PASS |
| Conv3×3 + Bias + ReLU | 7 输出行，4 核，超时 180 秒 | CPU 控制执行、独立参考结果检查 | 7,245 周期、7,821 条退休指令 | PASS |
| GEMM 取消 | 7 输出行，超时 180 秒 | 用户取消后不生成成功报告 | 进程退出码 130 | CANCELLED |

ADD 与 Conv 的宿主仿真耗时分别约 14.41 秒、69.63 秒，和芯片周期换算时间是不同指标。两者均复用已有仿真器和固件，没有在网页运行时编译。

## 自动化检查

执行：

```bash
python3 -m unittest discover -s experiments/multicore/tools/die -p 'test_*.py' -v
```

**13/13 通过**，包括 8 项工作台/进程控制测试和 5 项既有报告验证测试。覆盖参数和请求来源校验、静态资源、文件访问隔离、任务互斥、取消与回收、超时退出、失败后重新运行、中断历史恢复，以及性能数据汇总和嵌入安全。

另外检查 Python 语法、`node --check architecture.js` 和 `git diff --check`。此次没有修改硬件或 kernel，也未重复完整 RTL 回归；先前硬件回归记录见 [profiling_verification.md](profiling_verification.md)。
