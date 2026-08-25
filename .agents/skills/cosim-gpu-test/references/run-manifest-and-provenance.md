# Run manifest 与 provenance

自动化、repeat、environment row、非默认 binary，或必须证明未提交源码状态的测试
任务，执行前阅读本文。

## Manifest 门禁

自动执行前，在 `artifacts/<task-slug>/tests/run-manifest.tsv` 写入完整 row。当前严格
verifier 的 `cosim-matrix-verification/v2` schema 包含：

```text
row_id program program_source source_sha256 program_binary runner_argument
strict_acceptance mode repeat_count timeout_policy boot_timeout test_timeout guest_run_timeout
guest_test_prefix expected_hsa_interrupt gem5_binary gem5_config_args
output_dir artifact_dir artifact_dir_pattern matrix_path provenance_file
guest_bridge_policy status
```

- `program`、`program_source`、source SHA-256 与 `runner_argument` 必须是同一个精确
  operator identity。
- `strict_acceptance` 必须在 launch 前冻结。开发/诊断 row 使用 `0`；只有最终
  strict v2 候选才能使用 `1`，且不能在运行结束后把 `0` 改成 `1`。
- 正常 fresh-session acceptance 使用 `mode=pure_test`；保留 live state 的诊断 row
  使用 `mode=keep_alive_diagnostic`，不能标为 accepted。
- `guest_test_prefix` 必须记录 canonical effective 值；默认空输入规范化为
  `HSA_ENABLE_INTERRUPT=0`，原始输入另存于 invocation。`expected_hsa_interrupt` 必须为
  `0` 或 `1`。
- timeout、gem5 binary/config、输出目录、matrix 和 provenance path 必须在 launch 前
  冻结。
- 可接受的 leaf row 必须使用精确 `artifact_dir`、`repeat_count=1` 和
  `artifact_dir_pattern=-`。`--repeat`/`--all` wrapper 的 parent 只用于计划与调度；每个
  child 必须展开为独立 leaf row 后才能标记 accepted。
- `guest_bridge_policy` 使用稳定枚举 `artifact-local`；实际 Host bridge 必须是
  `<artifact_dir>/staging`，Guest path 必须是 `/mnt`。
- `status=accepted` 只用于已经通过全部 artifact gate 的 row。尚未执行时使用
  `planned`，失败或被替代时保留 row 并使用明确的非 accepted 状态。

默认 `COSIM_STRICT_ACCEPTANCE=0` 允许 working tree dirty，但仍要求 working
baseline lock、build metadata、当前 gem5 source fingerprint、binary hash 与 runtime
image 一致；其 patch/status/untracked artifact 用于重放，不能据此进入 strict v2
matrix。最终 row 必须以 `COSIM_STRICT_ACCEPTANCE=1` 启动新的 fresh session；此时顶层
仓库和 `gem5/` 必须 clean，gem5 HEAD 必须等于顶层 gitlink，tracked baseline lock
必须等于 `HEAD` 中的 blob。

Automation 只能执行字段完整的 planned row。不得从相近文件名、历史会话、partial
filter 或失败的 discovery command 推断 target。只有用户或 active plan 明确包含全部
operator 时才可用 `--all`。

Artifact path 是持久证据目录；Guest bridge 是 runner 暂存在该 artifact 下并挂载为
`/mnt` 的测试树。Bridge 不得依赖越出 share 的 Host symlink；其脚本、Guest output
和实际 binary 必须在 cleanup 前进入 row artifact。

## Artifact 唯一解析

优先使用 launch 前已知的 `artifact_dir`。若 wrapper 生成 child ID，则在执行/恢复阶段按
以下顺序把 parent 计划展开成精确 leaf row：

1. runner 原始输出；
2. 唯一目录中的 `runner-metadata.txt` 与 `matrix.tsv`；
3. `artifact_dir_pattern` 的唯一匹配。

0 个或多个匹配都属于 checkpoint failure。未记录唯一 exact path 前不能接受该 row；
展开后的 strict leaf 必须把 pattern 改为 `-`，不能让 verifier 重新猜目录。

## Acceptance 检查

Manifest 只是意图，不是执行证据。最终必须由同一 `artifact_dir` 证明：

- `runner-metadata.txt`、`matrix.tsv`、`verdict.json` 与
  `[COSIM_VERDICT]`/classifier output 一致；
- launch 前的 `runner-invocation.txt` 与 `launch-invocation.txt` 逐字段匹配 manifest，
  并证明实际 artifact、Guest bridge、gem5 binary/config 与原始 argv；
- manifest、`runner-invocation.txt`、`runner-metadata.txt`、local/top matrix 都记录
  `strict_acceptance=1`；run preflight 的 `run.strict_acceptance` 与整体状态均为 PASS；
- `guest-run.sh` 与 Guest log 证明实际 `TEST_TIMEOUT_SECS`，local/top matrix 同时记录
  三种 timeout；
- Guest `qemu.log` 中的 `[COSIM_ENV] HSA_ENABLE_INTERRUPT=<value>` 匹配 manifest；
- `patch/binary-provenance.txt` 含 `gem5_source_commit`、`gem5_binary`、
  `gem5_sha256`、归档 baseline lock path/hash 以及本次 test binary path/hash；
- `patch/source-snapshot.txt` 含 `head_commit`、`source_fingerprint`、runner hash、
  `repo_status_sha256`、tracked patch hash、gem5 status/patch/untracked hash、build meta
  hash 与 baseline lock hash，且没有 repository error；
- strict artifact 中 `patch/repo-status.txt`、`patch/repo.patch`、
  `patch/repo-untracked-files.txt`、`patch/gem5-status.txt`、`patch/gem5.patch` 和
  `patch/untracked-files.txt` 均为空；当前两个工作树在 verification 时仍 clean；
- 当前 top-level HEAD、gem5 HEAD、顶层 gem5 gitlink、source snapshot commit 与
  baseline lock 中的 commit 一致；归档 lock、working lock 和
  `HEAD:configs/cosim/gem5-baseline.lock` 内容 hash 相同；
- `cleanup-status.txt` 为 PASS，runner metadata 的 cleanup 为 `verified`；
- top-level matrix 只从该 artifact 的单一 row 汇总，并保持 program、环境、session、
  outcome、reason、binary 与 source provenance 一致。

Source snapshot 缺失或不自洽时分类为 evidence-incomplete。Binary provenance 能标识
executable，但单独不能重放未提交 source。

## Build 门禁

Review、RLCR、debug validation 与最终 evidence 都使用 `cosim-gpu-build` 的 build/
provenance gate。需要显式标准 gem5 binary 时仍通过 runner 传递：

```bash
PROGRAM=vector_add
COSIM_STRICT_ACCEPTANCE=1 ./scripts/run_cosim_tests.sh \
    --gem5-bin gem5/build/VEGA_X86/gem5.opt \
    --test-timeout 120 \
    --output-dir artifacts/task/case \
    "$PROGRAM"
```

Runner 在 launch 前写 source snapshot、runner/launcher invocation、归档 Guest script
以及 gem5 provenance；Guest 编译结束后再向同一 provenance 追加 test binary/hash。
门禁失败时按 `cosim-gpu-build` 处理，不在本文件复制或绕过 build metadata 规则。
Runner 当前只接受仓库当前 `gem5/` source tree 内的 binary；不得把另一 worktree
的 binary 与当前树的 Python config/commit 混合。需要 alternate worktree 支持时，必须先
扩展同树 mount、build metadata 和 verifier 合同，不能只传外部 `--gem5-bin`。
