---
name: cosim-gpu-test
description: 使用当前 fresh-session runner 在 cosim 上编译、运行并分类 tests/kernels 中的 GPU 算子时使用；不适用于普通单元测试或手动 Guest 命令。
---

# cosim-gpu 算子测试

`scripts/run_cosim_tests.sh` 是 Host 侧算子入口。operator 必须精确对应
`tests/kernels/<operator>.cpp`；每个 leaf run 都启动新的 QEMU+gem5 会话。

## 命令与参数

```bash
./scripts/run_cosim_tests.sh vector_add
./scripts/run_cosim_tests.sh --repeat 3 vector_add
./scripts/run_cosim_tests.sh --all
./scripts/run_cosim_tests.sh \
    --boot-timeout 240 \
    --test-timeout 60 \
    --guest-run-timeout 1800 \
    --output-dir artifacts/manual/vector-add \
    vector_add
```

当前 runner 自有参数为：

- `--all`、`--repeat N`、`--keep-alive`；
- `--session-name NAME`、`--screen-log PATH`；
- `--boot-timeout SECS`、`--test-timeout SECS`、
  `--guest-run-timeout SECS`；
- `--output-dir DIR`，且目录必须位于仓库 `artifacts/` 下并为空。

未知的双参数选项会透传给 `cosim_launch.sh`，例如 `--gem5-debug`、
`--qemu-trace`、`--num-cus` 和 `--num-gpus`。`--share-dir`、
`--artifact-dir`、`--evidence-test-id`、`--evidence-token` 由 runner
内部管理，不能透传。`--screen-log` 必须等于当前 leaf artifact 的
`qemu.log`，且不能与 `--all` 或 `--repeat` 一起使用。

`GUEST_TEST_PREFIX` 只接受空值、`HSA_ENABLE_INTERRUPT=0` 或
`HSA_ENABLE_INTERRUPT=1`。默认空值等价于 polling 模式 0。
`COSIM_STRICT_ACCEPTANCE` 默认为 0；只有明确要求 strict 候选时才设为 1，此时
顶层仓库和 `gem5/` 必须 clean，baseline lock 必须匹配，并显式包含 runner
要求的四个 debug flags：

```bash
COSIM_STRICT_ACCEPTANCE=1 ./scripts/run_cosim_tests.sh \
    --gem5-debug HSAPacketProcessor,GPUCommandProc,GPUDisp,GPUKernelInfo \
    vector_add
```

## Artifact 与结果

默认 leaf artifact 位于 `artifacts/<operator>/<run-id>/`。指定
`--output-dir` 时使用该目录；`--all` 和 `--repeat` 会在其下为每个 fresh
child 创建子目录。

关键文件：

| 文件 | 含义 |
| --- | --- |
| `qemu.log` | launcher、Guest 串口、编译和程序输出 |
| `gem5.log` | gem5 容器日志 |
| `gem5-evidence.tsv` | runner 管理的程序边界内 gem5 事件 |
| `runner-metadata.txt` | program、timeout、环境、退出码和 cleanup 状态 |
| `runner-invocation.txt`、`launch-invocation.txt` | 实际调用参数 |
| `guest-run.sh` | 本次 Guest 编译与执行脚本 |
| `verdict.json` | classifier 的 `outcome`、`reason` 与详细原因 |
| `matrix.tsv` | 当前 leaf 的单行摘要 |
| `cleanup-status.txt` | run-scoped 清理结果 |

成功要求目标程序退出 0、恰好输出一次精确的 `[PASS] <operator>`、没有
`[FAIL]`，且 runner/classifier/cleanup 都成功；结构化结果读取
`verdict.json`。需要重新读取一个既有 leaf 时可执行：

```bash
python3 -B scripts/classify_runs.py \
    --artifact-dir artifacts/vector_add/replace-with-run-id \
    --program vector_add --json
```

`--keep-alive` 只在 workload 成功后保留会话并打印 console FIFO；因为尚未
cleanup，它最终返回非零，不能当作普通 PASS。
