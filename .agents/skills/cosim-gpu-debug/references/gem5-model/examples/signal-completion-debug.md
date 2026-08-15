# HSA Signal Completion Debug (June 2026)

Four rounds of Codex adversarial review converged `sendCompletionSignal` from
"embarrassingly temporary" to "clean, approaching elegant."

## When you'll hit this again

- Adding a new signal completion path that needs deferred resource cleanup
- Reviewing code where `done_event` is threaded through multiple layers
- Debugging hipFree hangs after h2d→launch→d2h succeeds

## The problem

`submitVendorPkt` initiates a DMA chain that writes the HSA completion signal.
The packet must not be freed (`finishPkt`) until DMA completes. Original fix
threaded a bare `Event *done_event` through 6 public functions.

## Convergence

| Round | Rating | Finding |
|-------|--------|---------|
| 1 | Embarrassingly temporary | done_event through 6 public layers = parameter bloat |
| 2 | Acceptable but rough | Making functions private hides but doesn't fix coupling |
| 3 | Conditionally passes | Need single public entry, unique_ptr for buffers, explicit ownership |
| 4 | Clean, approaching elegant | Structure sound; remaining items are hygiene |

## Key insight

`dispatchStartTime[signal_handle]` with bare `[]` default-constructs zero-value
entries on missing keys — use `find()` instead. This was the pre-existing bug
that hipFree hang exposed: heap layout changes from the refactor shifted the
map's bucket distribution, creating a new zero-value entry that triggered an
unexpected early `finishPkt`.

## Artifacts

- `artifacts/fix-hsa-signal-ih-completion-2/` — RLCR workspace with all 4 rounds
- Session `~/.omp/agent/sessions/-cosim-gpu/2026-06-21T15-23-16-042Z*.jsonl` — full review dialogue

## Cross-references

- `../hsa-signal-completion-pattern.md` — final design pattern
- `../vmid-pasid-architecture.md` — call site semantics
