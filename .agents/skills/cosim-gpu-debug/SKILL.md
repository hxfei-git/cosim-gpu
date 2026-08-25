---
name: cosim-gpu-debug
description: 用于定位 cosim 中的 gem5 crash/assertion、hang、timeout、地址翻译 fault、non-PASS 测试、Guest wait，以及可能继发于 gem5 的 QEMU 退出；先保留证据，再判断第一个失败组件。
---

# Cosim 调试

本技能是 non-PASS cosim 行为的调试入口。流程以证据为先：先确定权威 artifact，判断
第一个失败组件，再只读取与已观察机制匹配的 reference。

## 入口门禁

调试请求直接使用本流程，不要搜索已退役的 debug skill 或旧路由名。把历史对话转换为
具体证据字段：artifact path、failing command、program identity、run ID、environment
row、first durable failure、live wait state、comparison row 和正在检查的 source
mechanism。

单独询问 environment variable 并不构成 debug evidence。Runner prefix 使用
`cosim-gpu-test`，ROCm 语义使用 `cosim-gpu-rocm-stack`；environment row 产生
non-PASS 或 live wait 后再回到本技能。

## Strict 与 diagnostic 边界

调试、dirty candidate 与 live-state sampling 默认使用
`COSIM_STRICT_ACCEPTANCE=0`。Runner 仍会校验 working baseline lock、build metadata、
当前 source fingerprint、binary hash 与 runtime image 一致，并归档 dirty state；这类
artifact 可用于定位和候选判断，但不能进入 strict v2 matrix。

现有 `strict_acceptance=1` row 的 artifact 不得因后续调试被改写或降级。修复后需要最终
accepted evidence 时，先提交 source 与 baseline lock、确认顶层仓库和 `gem5/` 都
clean，再通过 `cosim-gpu-test` 新建 fresh `COSIM_STRICT_ACCEPTANCE=1` row。不得把
default-mode `PASS`、standalone launch 或 keep-alive artifact 事后改标为 accepted。

## 证据权威

大型 artifact intake、历史对话复核或可能需要扫描许多 log 的任务使用
`cosim-gpu-info-gathering`。本技能应先根据紧凑 evidence map 推理，再打开 raw log。

先分类 artifact 状态：

| Artifact 状态 | 动作 |
|---|---|
| `verdict.json`、`matrix.tsv` 与 `patch/binary-provenance.txt` 存在 | 将归档文件作为行为证据；只有 strict 字段与 verifier 同时通过时才视为 accepted。 |
| Runner row 不完整且 matching process 存活 | 仅在 active plan 允许时采集有界 live sample。 |
| Runner row 不完整且 process 已死 | 保留旧目录，用 `cosim-gpu-test` 按原 manifest 目标新建 exact rerun row；不得覆盖旧 artifact。 |
| 旧 row 缺少归属判断所需的 gem5 log | 使用相同 program、environment、timeout 与 binary 创建固定 diagnostic rerun row。 |
| QEMU-visible failure 没有 matching gem5 evidence | 在取得 gem5 log 前保持 provisional classification。 |

接受 build 或 test evidence 前，记录精确 source 与 binary provenance。Build 规则使用
`cosim-gpu-build`，不要在本技能重复实现。

## Observable event 路由

对 timeout、wait 与 non-PASS row，先生成紧凑 event summary，再读取大范围 log。
`GPUWgProgress`、`HSAPacketProcessor` 与 `GPUCommandProc` 等 debug flag 是 event
source，不是 classification model；不同 log 可以填充同一 observation dimension。

使用 `references/analysis/observable-dimensions.md` 把 log 映射为标准维度：

- progress：dispatch count、completion count、active wave state；
- queue：read pointer、write pointer、dispatch pointer、packet completion；
- signal：completion signal address、signal write、interrupt/event wakeup；
- pressure：SQC retry、Ruby rejection、BufferFull、DMA/cache backpressure；
- failure：fatal、panic、assertion、translation fault、QEMU exit。

优先使用 `coverage.tsv`、`progress.tsv`、`queue.tsv`、`signals.tsv` 和简短的
`diagnostic-summary.tsv`。空白维度表示未采集；`UNEXPLAINED` 表示已经检查但没有发现
有用差异。缺失维度应指导下一项最小 debug flag，而不是触发广泛 log 扫描。

## 通用证据流程

提出 source edit 前收集：

- program path、run ID、interrupt mode、timeout value 与 exact runner command；
- 完整 gem5/QEMU/Guest log、verdict、matrix row 与 binary provenance；
- 第一条 durable failure，以及之前最后 50–100 条相关记录；
- 最近 passing comparison，或无法取得 comparison 的原因；
- 第一个不同的 object：packet ID、queue ID、doorbell offset、signal address、PASID、
  VMID、GPU virtual address、physical address、PTE value、callback ID 或 interrupt
  cookie。

先做定向搜索：

```bash
rg -n 'fatal|panic|assert|PM4|SDMA|VMID|PASID|doorbell|interrupt|signal|GART|PTE|translation|fault|timeout|Broken pipe|error_setv' \
  <artifact>/logs/gem5.log <artifact>/logs/qemu.log <artifact>/logs/guest
```

## 组件判断

根据第一条 durable evidence 选择后续动作：

| Evidence | 动作 |
|---|---|
| gem5 log 以 `fatal`、`panic`、assertion 或 container exit 结束 | 检查 gem5 log 并加载 `references/gem5-model/overview.md`。 |
| QEMU 出现 `error_setv`、vfio-user abort、socket close 或 `Aborted`，而 gem5 可能先退出 | 在 `references/qemu/error-setv-pattern.md` 与 gem5 log 证明相反前，把 QEMU 视为继发症状。 |
| gem5 log 出现 `User translation fault`、`GART`、unmapped page、VMID/PASID、PTE 或 doorbell evidence | 加载 `references/gem5-model/address-translation-fault.md`。 |
| Workload 存活但 output 与 progress counter 停止变化 | 保留 Guest，并加载 `references/analysis/live-wait-state.md`。 |
| Workload timeout 时 dispatch/completion 仍变化 | 编辑 model 前先分类为 throughput、scale 或 timeout-budget evidence。 |
| Non-PASS 没有 crash | 使用 `references/analysis/debug-analysis.md` 与 `references/analysis/observable-dimensions.md`。 |
| log 出现 cache、signal、PM4、VMID、PASID、SDMA、TLB 或 PWC | 加载匹配的 gem5 model reference。 |

对已知 MAP_QUEUES VMID assertion，检查：

```bash
grep -n 'assert.*vmid\|assert(queue_vmid)\|MAPQueues' \
  gem5/src/dev/amdgpu/pm4_packet_processor.cc
```

随后阅读：

- `references/gem5-model/vmid-assert-lesson.md`；
- `references/gem5-model/examples/vmid-assert-crash.md`；
- `references/gem5-model/vmid-pasid-architecture.md`。

## Timeout 与 wait 流程

Artifact summary 已指出缺失维度或 live state 后，阅读
`references/analysis/debug-workflows.md` 中的 timeout、wait-state 与 throughput
流程。

## Performance optimization 流程

Active objective 是 simulator efficiency 而非 functional correctness 时，阅读
`references/analysis/debug-workflows.md`。

## 地址翻译 fault 审查

gem5 在 crash 附近出现 `User translation fault`、`GART cosim`、unmapped page、PTE
diagnostic、VMID/PASID mismatch 或 unknown doorbell evidence 时使用本节，并阅读
`references/gem5-model/address-translation-fault.md`。

编辑前至少记录：

- failing program、run ID、HSA interrupt value 与最近 passing row；
- 第一条 gem5 fatal 与任何继发 QEMU socket/`error_setv` 症状；
- faulting GPU virtual address、可能的 physical address、PTE value、GART base、
  page-table base、VMID、PASID、queue ID 与 doorbell offset；
- failure 前的 PM4、SDMA 或 HSA packet sequence；
- fault 是否在不同 interrupt mode 下 deterministic。

同一 run ID 的 gem5 log 先出现 translation fatal 时，QEMU `error_setv`、EOF、broken
pipe 与 device-lost message 都是继发症状。

## Script 纪律

添加 debug script 或 source instrumentation 前阅读
`references/analysis/debug-workflows.md`。

## Guest 检查

Console transport 与 Guest command injection 使用 `cosim-gpu-guest`。采集：

- emulated GPU 的 PCI device state；
- loaded amdgpu module state；
- `cosim-gpu-setup.service` status；
- ROCm device visibility 与 agent enumeration；
- 完整及过滤后的 kernel log；
- target process ID、thread table、wait channel、kernel stack、file descriptor，以及
  target 仍存活时的 user-space backtrace。

## Patch readiness

判断 source edit 是否 ready 前阅读 `references/analysis/debug-workflows.md`。

## QEMU trace

需要 QEMU-side protocol evidence 时，用标准 vfio-user transport 做 diagnostic trace：

```bash
COSIM_STRICT_ACCEPTANCE=0 ./scripts/cosim_launch.sh --qemu-trace 'vfio_user_*'
```

记录 exact launch command 与 trace log。Standalone trace 只属于诊断；若该维度必须进入
最终证据，使用 runner passthrough 在 clean tree 上另建 strict row。

## References

- `references/analysis/debug-analysis.md`：事实记录与 comparison guide；
- `references/analysis/observable-dimensions.md`：observable dimension checklist；
- `references/analysis/live-wait-state.md`：live Guest wait-state sampling；
- `references/analysis/debug-workflows.md`：timeout、performance、script 与 patch-ready 流程；
- `references/gem5-model/overview.md`：gem5 MI300X model map；
- `references/gem5-model/discovery-log.md`：历史 model discovery；
- `references/gem5-model/cache-coherence-checkpoints.md`：TLB、PWC、SQC、GL2 checkpoint；
- `references/gem5-model/address-translation-fault.md`：GART、PTE、VMID/PASID 与 doorbell fault；
- `references/gem5-model/hsa-signal-completion-pattern.md`：HSA signal completion design；
- `references/gem5-model/vmid-pasid-architecture.md`：VMID/PASID semantics；
- `references/gem5-model/vmid-assert-lesson.md`：MAP_QUEUES VMID assertion lesson；
- `references/qemu/qemu-first-failure.md`：QEMU-first failure；
- `references/qemu/error-setv-pattern.md`：QEMU error propagation pattern。
