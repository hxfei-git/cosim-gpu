# HSA Signal Completion Pattern

Design pattern for threading completion callbacks through the HSA signal DMA
chain. Converged through 4 rounds of independent Codex adversarial review on
`fix-hsa-signal-only` (June 2026).

## Problem

A vendor packet (`submitVendorPkt`) initiates a completion signal DMA chain that
writes the HSA signal value to guest memory. The packet must not be released
(`finishPkt`) until the DMA chain completes — otherwise use-after-free.

The original fix threaded a bare `Event *done_event` pointer through 6 layers of
public functions, each gaining an extra parameter. This was rejected as parameter
bloat and cross-layer coupling.

## Converged design

Single public entry point. Internal chain is entirely private.

```cpp
// gpu_command_processor.hh — public
void sendCompletionSignal(Addr signal_handle, Event *done_event = nullptr);

// gpu_command_processor.hh — private
#include <memory>

struct HsaSignalUpdateContext {
    // Immutable — set at construction
    Addr signal_handle;
    int64_t diff = -1;           // completion signal always decrements by 1
    Tick start_ts = 0;

    // Mutable — accumulated during DMA chain
    std::unique_ptr<uint8_t[]> tmp;  // heap buffer, auto-cleanup on destruction

    // Caller-owned — ctx only schedules, never deletes
    Event *done_event = nullptr;

    void notify(GPUCommandProcessor *proc);    // FullSystem vs SE branching
    void complete(GPUCommandProcessor *proc);  // schedule(done_event) + delete this
};

// Private chain (all called only from within the struct or gpu_command_processor):
//   sendCompletionSignal → ctx = new HsaSignalUpdateContext
//     → updateHsaSignalAsync(ctx)
//       → updateHsaMailboxData(ctx)
//         → updateHsaEventData(ctx)
//           → updateHsaEventTs(ctx)
//             → updateHsaSignalData(ctx)
//               → ctx->notify(this)     // encapsulate FullSystem/SE branching
//               → ctx->complete(this)   // schedule(done_event) + delete this
```

## Call site semantics

Four call sites pass `sendCompletionSignal`. The distinction is not DMA vs
non-DMA (all paths go through DMA); it is **who needs resource cleanup after
DMA completes**:

| # | Call site | File | `done_event` | Reason |
|---|-----------|------|-------------|--------|
| 1 | `submitVendorPkt` | `gpu_command_processor.cc` | **non-null** | Signal DMA completes → must `finishPkt` to release packet |
| 2 | `dispatchKernelObject` | `gpu_command_processor.cc` | `nullptr` | Only writes completion signal; no packet to release |
| 3 | `notifyWgCompl` | `dispatcher.cc` | `nullptr` | Workgroup completion notification; no resource to defer-clean |
| 4 | barrier packet | `hsa_packet_processor.cc` | `nullptr` | Doorbell-triggered; no packet lifecycle involved |

## Review convergence

The design was iterated through 4 rounds of independent Codex review. Each round
produced a rating that drove refinement:

| Round | Rating | Key finding |
|-------|--------|-------------|
| 1 | "Embarrassingly temporary" | `done_event` bare pointer threaded through 6 public functions — parameter bloat, cross-layer coupling |
| 2 | "Acceptable but rough" | Moving functions to private hides the bloat but doesn't fix internal coupling |
| 3 | "Conditionally passes" | All intermediate functions must be private; single ownership for tmp buffer (unique_ptr); caller owns done_event (ctx only schedules) |
| 4 | "Clean, approaching elegant" | Structure sound; remaining items are engineering hygiene (unique_ptr vs raw, find() vs bare [], cleanup audit) |

## Ownership rules

- `ctx->tmp`: owned by context, released automatically on destruction
- `ctx->done_event`: caller-owned; context only calls `schedule()`, never `delete`
- Context itself: heap-allocated in `sendCompletionSignal`, self-deletes in `complete()`
- All early-exit paths (DMA read failure, address resolution failure, null event,
  functional read path, callback not triggered) must reach `complete()` or
  equivalent failure cleanup

## Cross-references

- `overview.md`: HSA layer and signal flow overview
- `../../../cosim-gpu-rocm-stack/SKILL.md`: HSA signals from guest/driver side
- `discovery-log.md` entry 6: interrupt VMID routing discovery that preceded this design work
