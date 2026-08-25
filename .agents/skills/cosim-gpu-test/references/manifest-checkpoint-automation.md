# Manifest checkpoint 自动化

由 `tests/run-manifest.tsv` 驱动的 cosim test/debug matrix 因中断、context compaction
或跨多个 Codex turn 执行时使用本文。

## 权威顺序

1. 当前 manifest 的精确 row 与 status；
2. per-row `runner-metadata.txt`、`verdict.json`、`matrix.tsv`、
   `cleanup-status.txt`、`runner-invocation.txt`、`patch/binary-provenance.txt` 和
   `patch/gem5-baseline.lock`；
3. top-level matrix 与 verifier output；
4. 已经转换为 artifact fact 的对话观察。

存在归档 runner 文件时，不得用 chat、partial terminal output 或 live container output
覆盖它们。

## Row 状态

执行动作前分类每一行：

- `complete-diagnostic`：artifact path 唯一，verdict/matrix/metadata/cleanup 完整，
  实际 Guest environment 与 working provenance 自洽，但 `strict_acceptance=0`；它可用于
  debug/candidate 判断，不能进入 strict v2 matrix；
- `complete-strict`：除完整 artifact 外，manifest、invocation、metadata、local/top
  matrix 都证明 `strict_acceptance=1`，两个源码树 clean，tracked baseline lock 等于
  `HEAD`，并已通过 strict verifier；
- `incomplete-live`：缺少 terminal verdict，且同一 run ID 的 runner、launcher、QEMU
  或 gem5 仍存活；
- `incomplete-dead`：缺少 terminal verdict，且已确认没有匹配 live process；
- `invalid`：program identity、环境、binary、output path、source snapshot 或
  provenance 与 manifest 冲突；
- `complete-nonpass`：已有 terminal non-PASS artifact，必须进入 debug，不能当作
  incomplete 直接覆盖。

## 动作

- `complete-diagnostic`：保留并汇总为诊断证据；不得改写为 accepted。需要最终结果时，
  先提交候选与 baseline lock、清理两个工作树，再新增 fresh strict row。
- `complete-strict`：用严格 verifier 复核后汇总；不要重复运行。
- `incomplete-live`：继续等待 runner artifact；只有 active debug plan 明确要求时才做
  有界 live-state sampling。
- `incomplete-dead`：保留旧目录，使用新的 exact output directory 重跑同一 manifest
  目标；把旧 row 标为 superseded 并新增 row，不能覆盖原始 artifact。
- `invalid`：记录 preflight/provenance failure，不替换为相近 program、timeout、binary
  或 environment。
- `complete-nonpass`：记录 row、artifact、verdict reason，转入 `cosim-gpu-debug`；修复
  后新增 same-target rerun row。

## Live process 判定

只按 run-scoped identity 匹配：runner session/control dir、launcher PID/process group、
container label、socket、shared memory 与 manifest 必须属于同一 run ID。不要按宽泛的
`qemu`/`gem5` 名称终止进程，也不要在 launcher 仍存活时直接运行 manifest cleanup。

## 长 timeout row

验证 manifest、`runner-invocation.txt`、`launch-invocation.txt`、
`runner-metadata.txt`、`guest-run.sh`、Guest log 以及 local/top matrix 都证明实际 timeout。
Host deadline 变长但 Guest script 仍使用旧值时，分类为 invocation defect，不是模型
证据。有效 timeout row 从归档 log 提取最后 completion/dispatch count、active wave、
fatal/panic 与 binary provenance；不要只读 live tail。

## Evidence 记录

至少记录：

- manifest row ID、status 与 exact output directory；
- interrupted row 的 run-scoped live/dead 判定；
- final verdict、matrix 与 cleanup source file；
- manifest、runner invocation/metadata 与 local/top matrix 中的
  `strict_acceptance`，以及 strict verifier 结果；
- Guest 观察到的 effective environment；
- gem5 source commit/subject、binary path/hash 与 test binary hash；
- source fingerprint、runner hash 与 tracked/untracked replay artifact；
- baseline lock archive/hash；若为 strict row，还要记录两个 clean tree、gem5 gitlink 与
  tracked `HEAD` lock 的一致性；
- Guest 内部实际 timeout 是否得到证明。
