# 编译好的算子：一条命令运行并查看芯片执行

当前分支支持 ADD、GEMM+bias+ReLU、Conv3x3+bias+ReLU，以及两个复杂算子的串行/并行组合。统计来自真实控制 CPU 与四核 Coral 的 RTL 仿真，指令数是设备的 **minstret 退休计数**，不再仅展示向量 MAC 派发次数。

## 最短用法

在 coralnpu 根目录执行。第一次或修改代码后先构建：

```bash
python3 experiments/multicore/tools/ncoral.py build --jobs 2
```

以后只运行已编译镜像，不启动编译器：

```bash
python3 experiments/multicore/tools/ncoral.py op list
python3 experiments/multicore/tools/ncoral.py op run gemm --open
python3 experiments/multicore/tools/ncoral.py op run conv --open
python3 experiments/multicore/tools/ncoral.py op run add --elements 37 --rounds 2 --open
```

`--open` 用系统浏览器打开生成的 HTML；无桌面时省略，复制终端打印的 `Interactive report` 路径自行打开。生成报告没有网络依赖。运行完成后才能回放，播放按钮不是运行时调度控制器。

单独运行 GEMM 或卷积时使用四个核，按输出行切分；`--rows 7` 分为 `2+2+2+1`，`--rows 1` 也会启动四核，其中三个为空分片。单算子的最大输出行数为 8，其他维度仍采用已有固定测试形状。加法最多 128 个元素，`--rounds 2` 演示 `C=A+B → D=C+B` 的依赖。

```bash
python3 experiments/multicore/tools/ncoral.py op run gemm --rows 7 --clock-mhz 500
python3 experiments/multicore/tools/ncoral.py op run pair --schedule serial --open
python3 experiments/multicore/tools/ncoral.py op run pair --schedule parallel --open
```

`pair` 保持 GEMM 用核 0/1、卷积用核 2/3。`--clock-mhz` 仅用于时间换算，不改变 RTL 的计算逻辑或性能模型；500 MHz 是默认**假设频率**，不是芯片物理实现的达成频率。

## 报告怎样看

- **算子时间线跨度**：同一算子最早 START 到最晚 END 的全局周期差。四核并行不能把四个核的周期相加当成算子耗时。选择“全部算子”时展示整个执行区间，包括依赖间隔。
- **时间换算**：`时间（微秒）= 周期 / 频率（MHz）`。页面可修改假设频率，周期和指令数保持不变。
- **退休指令总数**：各核采样区间的 `minstret_end - minstret_begin` 求和，包含标量与 RVV 指令；一个向量指令按一条统计，不按 lane 数、元素数或 MAC 数展开。
- **采样周期**：每核 `mcycle_end - mcycle_begin`，与 START/END 日志包络不同。
- **宿主仿真耗时**：电脑执行 Verilator 程序的秒数，与芯片时间独立列出。

时间线可以播放、拖动、按算子筛选，表中的“查看”可定位到具体核。每次运行会生成新的目录，保留旧报告；`--output /absolute/new-directory` 可指定新目录，已存在时拒绝覆盖。

## 统计范围与精度

复杂 kernel 在 START 日志之后读取 `mcycle/minstret`，完成向量配置、权重打包、计算和行日志后，执行 fence 并再次采样；随后写回性能记录，最后输出 END 日志。ADD 采用相同的开始/结束采样方式。采样包含 CSR 边界指令及其附近的少量测量开销，没有尝试用固定常数扣除。

因此：表格中的计数是**明确采样区间内的硬件计数**，不是零开销的纯算术计算计数；图中包络还包含性能记录写回和日志开销。空分片也有启动与采样开销，不能把它视为零指令。退休计数来自 Coral 的 CSR 实现及退休缓冲 `nRetired`，与已有 `rvvMacIssued` 派发计数各自独立。

本模型每次测试最多 2,000,000 个仿真周期，计数采用 32 位低半部分并按模 2^32 求差；在此预算和核内发射宽度下不会跨过完整的 32 位计数周期。扩展长时间运行之前需要改成 RV32 的高低高一致性读取。

各核性能记录位于共享 `0x20003000` 开始的 8×32B 区域，使用 `PROFILE_MAGIC` 和 ABI 1。两个 ADD invocation 各占四条记录。控制固件启动时清除相应 magic，设备在结果字段和 fence 之后最后发布 magic。测试台仅读取记录和 RTL 事件，不执行算子或替设备计数。

## 编译包与产物

当前“已编译算子”指 `build-code` 生成的 **controller.bin 编译包**：里面包含控制固件及两个 Coral kernel 镜像。参数、布局和数值参考与这些已知算子匹配。这不是任意未知 ELF 的通用装载器。

可以把整个 `die/logs/firmware/` 复制到其他目录，再运行：

```bash
python3 experiments/multicore/tools/ncoral.py op list --firmware /path/to/firmware/controller.bin
python3 experiments/multicore/tools/ncoral.py op run gemm \
  --firmware /path/to/firmware/controller.bin --open
```

编译包必须保留 `report.json` 和报告列出的 ELF/bin 文件，程序校验所有二进制哈希、RVV 编译证据和 profiling ABI。`op run` 有意允许运行与当前源码不同的已编译版本，并在报告中保存镜像和仿真器 SHA256；旧 `run/demo/test` 入口仍要求源码与编译配方匹配。不接受缺少采样能力的历史镜像。

默认产物位于 `experiments/multicore/tools/die/logs/operators/<operator>_<time>/`；如果设置了 `NCORAL_WORK_DIR`，位于该目录下的 `die/logs/operators/`。

| 文件 | 用途 |
|---|---|
| `report.html` | 离线交互报告 |
| `profile.json` | 已校验的采样、仿真正确性结果、二进制版本和换算频率 |
| `counters.json` | 仿真器从设备记录和 RTL 时间戳生成的原始采样 |
| `run.log` | 完整设备日志和数值检查结果 |
| `execution.json` | 执行命令、起止时间、截止时间与退出码 |

重新打开历史数据不需要仿真：

```bash
python3 experiments/multicore/tools/ncoral.py op view /path/to/profile.json --open
```

每次执行前打印截止时间，默认最多 420 秒；`--run-timeout` 可调整，芯片内等待和最大仿真周期限制仍生效。只有独立数值检查和性能记录检查均通过，才生成成功报告。失败或超时仅保留诊断日志，不生成伪成功的 HTML。

## 修改位置与验证

- `device/profile.h`：CSR 采样和共享记录 ABI。
- `device/add.c`、`device/complex.c`：算子采样区间。
- `die/firmware/runtime.c`：记录分配及单算子四核分片。
- `die/profile_check.h`：记录校验、时间线和指令汇总。
- `tools/die/operators.py`、`operator_report.html`：预编译包运行入口和交互报告。

`ncoral.py test` 扩展为 23 项，新增四核单 GEMM/卷积与空分片场景，每个成功用例还检查性能记录。不同计算量的 ADD/GEMM/卷积必须得到递增的退休指令数。报告层另有 5 项校验测试：

```bash
python3 -m unittest discover -s experiments/multicore/tools/die -p test_operator_reports.py -v
python3 experiments/multicore/tools/ncoral.py test --jobs 3
```

本轮实测与验证范围见 [profiling_verification.md](profiling_verification.md)，包含版本化的源码/镜像哈希和指令数递增结果。
