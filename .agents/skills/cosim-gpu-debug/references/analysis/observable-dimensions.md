# Observable Dimensions

Which observable dimensions to compare between working and failing cases.
Read with `../gem5-model/` references for source-level checkpoints.

## Event Model

Do not bind analysis to one debug flag's text format. Convert available logs
into a small event model first, then reason from the event tables.

Recommended event row:

```text
row_id	event_type	source	line	object_id	value	confidence
```

`event_type` should be one of:

- `progress.dispatch`
- `progress.complete`
- `progress.active_wave`
- `queue.read_ptr`
- `queue.write_ptr`
- `queue.dispatch_ptr`
- `queue.packet_complete`
- `queue.barrier_state`
- `signal.addr`
- `signal.write`
- `signal.interrupt`
- `pressure.fetch_retry`
- `pressure.ruby_reject`
- `failure.fatal`
- `failure.assert`
- `failure.translation`
- `failure.qemu_exit`

Different debug flags may produce the same event type. For example, a workgroup
progress log and a packet processor log can both prove that execution advanced;
they should fill the same progress dimension rather than create separate
classification rules.

## Compact Tables

Generate these small tables when possible:

```text
coverage.tsv: row_id dimension status source
filter_coverage.tsv: row_id filter_name filter_expr covered_min covered_max observed_min observed_max uncovered_count status
progress.tsv: row_id kernel_id dispatched completed last_change_line rate_hint
queue.tsv: row_id queue_id read_ptr write_ptr dispatch_ptr barrier_state last_event
signals.tsv: row_id signal_addr wrote_signal interrupt_seen source
```

`status` values:

- `observed`: dimension has concrete evidence
- `missing`: not collected
- `unexplained`: checked, no useful difference found

Agents should read `coverage.tsv` and `diagnostic-summary.tsv` before opening
large logs.

`filter_coverage.tsv` is required whenever instrumentation or log extraction
uses object filters such as sequence ranges, queue ids, kernel ids, CU ids,
wavefront ids, addresses, or packet ids. A negative observation is valid only
when `uncovered_count` is zero for the final objects being explained.

## Signal / Interrupt

| Signal | Working | Failing |
|--------|---------|---------|
| EOP interrupts | N | M |
| Trap interrupts | N | M |
| Completion signals | N | M |

## PM4 packets

| Packet type | Working count | Failing count |
|-------------|---------------|---------------|
| MAP_PROCESS | N | M |
| RUN_LIST | N | M |
| WRITE_DATA | N | M |
| INDIRECT_BUFFER | N | M |
| RELEASE_MEM | N | M |

## Translation / VMID

| Dimension | Working | Failing |
|-----------|---------|---------|
| VMID at last EOP | N | M |
| PASID at signal write | N | M |

## Cache coherence

| Cache layer | Working invalidation count | Failing invalidation count |
|-------------|---------------------------|----------------------------|
| PWC (page walk cache) | N | M |
| TLB (per-VMID) | N | M |
| SQC (scalar L1) | N | M |
| GL2 / L2 | N | M |

## Memory / dispatch

| Dimension | Working | Failing |
|-----------|---------|---------|
| Kernarg pages mapped | N | M |
| Dispatch grid size / dims | N | M |
| Signal addresses allocated | N | M |
| Workgroups dispatched | N | M |
| Workgroups completed | N | M |
| Progress still changing | yes/no | yes/no |

## Queue / packet state

| Dimension | Working | Failing |
|-----------|---------|---------|
| HSA queue read pointer | N | M |
| HSA queue write pointer | N | M |
| HSA dispatch pointer | N | M |
| Packet completion observed | yes/no | yes/no |
| Barrier state after completion | clear/set | clear/set |

## Pressure / throughput

| Dimension | Working | Failing |
|-----------|---------|---------|
| SQC fetch retry count | N | M |
| Ruby rejection count | N | M |
| DMA or cache backpressure | N | M |
| Estimated completion time | seconds | seconds |

## Source checkpoints

For exact gem5 file:line references per cache/translation layer, use
`../gem5-model/cache-coherence-checkpoints.md`.

## Completeness rule

Each relevant dimension must have a value or `UNEXPLAINED` before a source edit.
`UNEXPLAINED` means no difference was found after checking that dimension.
A blank value means the observation has not been collected yet.

`coverage_insufficient` means the diagnostic ran but did not cover the final
objects that matter. It is not equivalent to `UNEXPLAINED` and must not be used
as evidence that an event was absent.

For timeout rows, a source edit is not ready if the only fact is
`TIMEOUT_WAIT`. First determine whether progress still changes, all GPU work
completed before host wait, or the row lacks enough observation coverage.
