# 诊断与矩阵运行

长 timeout、performance row、interrupt-mode matrix、repeat run 或中断的 matrix
自动化应阅读本文。

## 流程 checkpoint 执行

矩阵运行中断后，从以下证据重建状态：

- `artifacts/<task-slug>/tests/run-manifest.tsv`；
- top-level `matrix.tsv`；
- 仍存活的 `run_cosim_tests.sh`、`cosim_launch.sh`、QEMU 与 gem5 process；
- 每个 row 的 `verdict.json` 或 `[COSIM_VERDICT]`；
- binary provenance 与 effective environment row。

没有 verdict 的中断 row 属于 incomplete。若已无 live process，使用精确 manifest
command 和新的 exact output directory 重跑。若 per-row artifact 已有 verdict 而
top-level matrix 缺少该 row，只有在 artifact 匹配 manifest 后才能汇总。要进入
strict v2 matrix，还必须证明该 leaf row 从 launch 前起就是
`strict_acceptance=1` 并通过 strict verifier；默认 dirty replay 只能进入诊断汇总。
否则新增 exact rerun row，不得改写旧 artifact。

固定 row 较多或 matrix 只完成一部分时，还要阅读
`manifest-checkpoint-automation.md`。

## Diagnostic row

确认 timeout 或调查 throughput 时，启动一个固定 manifest row 并等待 runner
artifact。不得把轮询到的 live output 当作最终证据；阅读大型 log 前先使用
`cosim-gpu-info-gathering` 汇总归档内容。

Diagnostic row 默认使用 `COSIM_STRICT_ACCEPTANCE=0`。只有 active plan 明确要求
clean-tree final acceptance rerun 时才使用 `1`。Diagnostic `PASS` 仍只是 candidate
evidence，不能改标为 strict v2 row。

使用 debug flag 时记录：

```text
diagnostic_purpose=confirm_hang|throughput_probe|queue_probe|signal_probe
observation_dimension=progress|queue|signal|pressure|failure
debug_flags=<exact gem5 debug flags>
```

优先使用已有的紧凑表：

- `coverage.tsv`；
- `filter_coverage.tsv`；
- `progress.tsv`；
- `queue.tsv`；
- `signals.tsv`；
- `diagnostic-summary.tsv`。

若 dispatch 或 completion counter 仍在变化，把 timeout 分类为 `slow_progress`。只有
counter、Guest output 和相关 queue/signal observation 都停止变化，或缺少的维度需要
定向补采时，才能将其视为 wait candidate。

若 diagnostic filter 没有覆盖最终等待对象，标记为 `coverage_insufficient`，不能据此
声称对应 event 不存在。

## Performance row

把 runner wall time、Host CPU time、Guest workload window、event/completion count、
verdict、binary hash、source fingerprint 与 candidate diff 保存在同一 artifact set。

Dirty candidate/performance 探索使用 `strict_acceptance=0`。选定并提交 candidate 后，
必须在两个 clean tree 上用新的 `COSIM_STRICT_ACCEPTANCE=1` row 重测最终 accepted
结果；不能复用 diagnostic timing artifact 作为 acceptance evidence。

解析 `/usr/bin/time -v` 时保留原始 elapsed text，并正确转换 `SS`、`M:SS`、
`H:MM:SS`。用第一个 `": "` 分隔 label 与 value，避免破坏 elapsed value。

同时报告 runner wall-clock 变化和可用的 target workload window。若内部 event count
改善而 runner wall time 不变，将其分类为局部 cleanup 或次要优化。

## Matrix run

多个 program、interrupt mode 或 repeat 应在 task artifact 目录下创建一个 top-level
`matrix.tsv`，每个 accepted run 追加一行。

只有 manifest、runner evidence、local/top matrix 与 verifier 都证明
`strict_acceptance=1` 的 row 才能标为 accepted。默认模式或 dirty replay 必须保留在
单独的 diagnostic matrix/status 中。

Interrupt-mode strict row 示例：

```bash
COSIM_STRICT_ACCEPTANCE=1 GUEST_TEST_PREFIX="HSA_ENABLE_INTERRUPT=0" \
    bash scripts/run_cosim_tests.sh <program>
COSIM_STRICT_ACCEPTANCE=1 GUEST_TEST_PREFIX="HSA_ENABLE_INTERRUPT=1" \
    bash scripts/run_cosim_tests.sh <program>
```

Interrupt mode 只能从 Guest log 的 `[COSIM_ENV] HSA_ENABLE_INTERRUPT=<value>`，或由
该行生成的 matrix data 接受。

Local matrix 至少包含：

```text
program	hsa_interrupt	run	session_id	outcome	exit_code	reason	artifact_dir	boot_timeout	test_timeout	guest_run_timeout	strict_acceptance
```

任何 non-PASS row 都要报告 row、artifact directory 与 verdict reason，随后进入
`cosim-gpu-debug`。全部 row PASS 时，报告 pass count、gem5 commit、gem5 binary
hash、matrix path、strict verifier result 与已证明的 `strict_acceptance` value。
