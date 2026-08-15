# QEMU Proxy Double-Error Crash (June 2026)

`error_setv` assertion failure in `hw/vfio-user/proxy.c` during cosim launch.
This was a secondary effect of gem5 crashing — QEMU's double-error-set pattern
converted a socket disconnect into a SIGABRT instead of a clean error report.

## When you'll hit this again

- QEMU crashes with `error_setv: Assertion '*errp == NULL' failed`
- gem5 container exits unexpectedly during guest driver init
- The first failing component is on the gem5 side; QEMU is a secondary reporter

## First failing mechanism

```c
// proxy.c:234 — anti-pattern
ret = qio_channel_readv_full(proxy->ioc, &iov, 1, fdp, numfdp, 0, errp);
// ↑ already set *errp on ECONNRESET
if (ret < 0) {
    error_setg_errno(errp, errno, "failed to read header"); // double-set → SIGABRT
}
```

## Fix pattern

```c
Error *local_err = NULL;
ret = qio_channel_readv_full(proxy->ioc, &iov, 1, fdp, numfdp, 0, &local_err);
if (ret < 0) {
    error_propagate_prepend(errp, local_err, "failed to read header: ");
}
```

## Debugging method

GDB conditional breakpoint to filter out harmless `errp == NULL` paths (SGX init):
```
break error.c:62 if *errp != NULL
```

## Upstream status

Same bug exists in upstream QEMU master (`gitlab.com/qemu-project/qemu`).

## Artifacts

- `artifacts/pr4-qemu-error-setv-debug/` — full GDB debugging session
- Session `~/.omp/agent/sessions/-cosim-gpu/2026-06-21T12-38-21-819Z*.jsonl` — investigation dialogue

## Cross-references

- `../error-setv-pattern.md` — error_setv mechanism and fix
- `../../gem5-model/examples/vmid-assert-crash.md` — the gem5-side cause
