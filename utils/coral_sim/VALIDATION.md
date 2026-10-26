# 本次构建与验证

- 日期：2026-10-26，北京时间。
- 分支：`coralnpu-sim`，基于主分支提交 `517f4c09bd5aabb4e8a16436d8853c912a24790b`。
- 环境：Linux x86_64、Bazel 9.1.0、Clang/LLD 17、GCC 13；复用本机 Bazel 缓存及同级 `.tools/system-zlib` 替代仓库。
- 实际执行：`./utils/coral_sim/build.sh --target all --test`。根目录 `./build-sim.sh` 直接转发到这个实现。

| 验证 | 结果 |
| --- | --- |
| 功能仿真器 Bazel 构建 | 成功，15:31:07–15:40:20 |
| RTL 仿真器 Bazel 构建 | 成功，15:40:20–15:47:11 |
| 功能 ELF 回归 | 15/15 通过，含本机可用的 Linux QEMU 两例 |
| RTL 正确性及错误检测 | 5/5 通过 |
| RTL 控制台输出 | 6/6 通过 |
| 根目录脚本从 `/tmp` 调用 `--help` | 通过 |
| 生成的 `bin/coral-sim` 从 `/tmp` 执行 hello ELF | 原始打印正确，1848 条动态指令，退出码 0 |
| 两份第三方源码 SHA256 清单 | 均与文件逐项一致 |

可在同一检出中查看 `out/coral-sim/logs/build-iss.json`、`build-rtl.json` 的实际命令及截止时间，以及 `out/coral-sim/functional-validation/verification.json`、`verification.json`、`console-validation/verification.json` 的逐项结果。这些机器相关生成文件不提交 Git。

本次验证覆盖现有工作区的实际构建与执行；尚未另外测试没有缓存的干净机器。脚本提供 PATH 工具查找和标准上游依赖回退，依赖及定制参数见 [README.md](README.md)。
