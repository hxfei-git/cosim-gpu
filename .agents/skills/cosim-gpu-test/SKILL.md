---
name: cosim-gpu-test
description: 在 cosim 上运行并分类 GPU 程序时使用；要求精确 program identity、fresh-session runner、verdict artifact 与 provenance，并在计划内测试前先使用 cosim-gpu-flow-plan。
---

# Cosim 测试

在 cosim 上执行 GPU 程序并分类结果。Test runner 负责 launch、Guest 执行、artifact
路由、source snapshot、provenance、matrix row、verdict 与 cleanup。

`cosim-gpu-launch` 只用于手动 boot 或交互检查。需要 program result、matrix row、
verification result 或 PR-grade test artifact 时使用本技能。

## 快速入口

```bash
bash scripts/run_cosim_tests.sh vector_add
bash scripts/run_cosim_tests.sh --all
bash scripts/run_cosim_tests.sh --test-timeout 120 --output-dir artifacts/<slug> vector_add
bash scripts/run_cosim_tests.sh --keep-alive --output-dir artifacts/<slug> vector_add
COSIM_STRICT_ACCEPTANCE=1 bash scripts/run_cosim_tests.sh \
    --output-dir artifacts/<slug>/strict-v2 vector_add
```

`scripts/run_cosim_tests.sh` 有两种会话模式：

- 默认模式：fresh cosim session、Guest workload、分类、artifact 归档和已验证清理；
  默认 `COSIM_STRICT_ACCEPTANCE=0`，适合开发、候选验证和可重放的 dirty-tree
  诊断，但不能进入 strict v2 matrix。
- `--keep-alive`：成功运行 workload 后保留 console pipe 供 `cosim-gpu-guest`
  诊断。由于 cleanup 尚未完成，该命令返回非零，临时 verdict 不能作为 PASS；保存
  诊断证据后必须按精确 manifest 清理。

默认测试始终归档证据并清理 live session。

## 证据级别

`COSIM_STRICT_ACCEPTANCE` 只允许 `0` 或 `1`：

- `0` 是默认开发/诊断模式。Runner 仍要求 working baseline lock、build metadata、
  当前 gem5 source fingerprint、binary hash 和 runtime image 完全一致，并归档 dirty
  状态供重放；该 row 即使得到 program `PASS` 也不是 strict v2 accepted row。
- `1` 是最终验收门禁。除了上述一致性，还要求顶层仓库与 `gem5/` 都 clean、当前
  gem5 HEAD 与顶层 gitlink 一致，并要求 tracked
  `configs/cosim/gem5-baseline.lock` 与 `HEAD` 中的 blob 相同。

最终 accepted row 必须在 manifest 中预先写明 `strict_acceptance=1`，并由
`runner-invocation.txt`、`runner-metadata.txt`、local/top matrix 和 strict verifier
一致证明。不得把先前的 diagnostic row 或 dirty replay 原地改标为 accepted；应在
clean tree 上启动新的 strict run。`--keep-alive` 无论 flag 值为何都只能用于诊断。

## 必需门禁

启动命名 program 前，必须证明精确 program identity。本 runner 只接受
`tests/kernels/<snake_case>.cpp` 中存在的 operator。涉及本地 kernel、variant、
timeout 估计、device-side `printf` 或 `GUEST_TEST_PREFIX` 时，阅读
`references/program-identity-and-guest-env.md`。

执行自动 row、repeat、environment matrix、非默认 binary，或用证据证明未提交源码
前，阅读 `references/run-manifest-and-provenance.md`。

长 diagnostic row、performance row、matrix run 或中断的测试自动化应读取
`references/diagnostic-and-matrix-runs.md`。

Build readiness 与 binary provenance 决策使用 `cosim-gpu-build`；本文件不复制其
metadata 规则。

## 自动执行规则

program identity、manifest row 和所需 provenance 检查完成后，直接运行
`scripts/run_cosim_tests.sh`。标准测试、repeat、interrupt-mode matrix row、timeout
probe、当前范围内的 post-fix rerun、标准 incremental build 或 run preflight 不需要
再次询问。

只有改变 target program、改变固定 environment value、使用非标准 binary、full/cold
rebuild、删除仓库 cleanup script 范围外内容，或在计划未授权时切换到 live debug
模式，才需要询问。

## 结果分类

测试由 `scripts/classify_runs.py --json` 分类。代理必须读取 test artifact 中的
`[COSIM_VERDICT]` 或 `verdict.json`，并原样报告 outcome；不得用人工解释 log 覆盖
verdict artifact。

| Outcome | 含义 | 动作 |
|---|---|---|
| `PASS` | Program 已执行、输出校验通过、exit 0 | 记录并继续 |
| `FAIL` | 任何非 PASS 结果 | 按 verdict reason 路由 |

使用 verdict `reason` 路由，例如 timeout、wrong result、simulator death、boot
timeout 或 invocation error。若 QEMU 或 gem5 在 Guest 测试开始前退出，应将其视为
launcher/transport 证据，而不是 program result。

对已有 artifact 重新分类：

```bash
python3 scripts/classify_runs.py --log-dir artifacts/<slug>/logs [--matrix artifacts/<slug>/matrix.tsv]
```

## 与其他技能的关系

- 计划内测试前：`cosim-gpu-flow-plan` 的 test 类型。
- 接受证据前：`cosim-gpu-build` 的 build/provenance gate。
- 启动前：`scripts/cosim_preflight.sh run`；不得把无参数 cleanup 当成 preflight。
- 任何 non-PASS row：`cosim-gpu-debug`。
- 输出权威：`artifacts/<slug>/` 下的 per-row artifact、matrix row、Guest/gem5/QEMU
  log、verdict、source snapshot 与 binary provenance。

最终 matrix 的每个 accepted row 都必须证明 program identity、outcome、Guest log、
artifact path、三种 timeout、runner/launcher invocation、Guest bridge、gem5 commit、
gem5 binary path/hash、baseline lock、`strict_acceptance=1` 与两个 clean tree，并通过
`cosim-matrix-verification/v2`。Manifest value 只是 pre-run contract，不是执行证明；
`--repeat`/`--all` 的 parent 结果必须展开为精确 leaf row 后才能进入 strict matrix。
