# Live Wait-State Sampling

Use this reference when a target is alive but no longer makes progress. The
goal is to preserve the wait object and capture two comparable state samples.

## Launch Rule

Do not run the normal test timeout wrapper for a known wait-state
investigation. It can kill the process that contains the evidence.

Use the runner only to create the live environment:

```bash
scripts/run_cosim_tests.sh --hang-env --output-dir <case_dir> <program>
```

Build the exact guest command from the target identity already proven by
`cosim-gpu-test`:

- local kernel: `cd /mnt/tests && ./build/<program>`
- ROCm example: `cd /mnt/external/rocm-examples/<case-dir> && ./<built-binary>`

## Guest Session

Before starting the target, verify that `tmux` exists in the guest as root:

```bash
printf '%s\n' "${COSIM_GUEST_SUDO_PASSWORD:?set COSIM_GUEST_SUDO_PASSWORD}" | sudo -S bash -lc 'command -v tmux'
```

If this fails, mark the disk image or guest setup invalid for live wait-state
debugging.

Use a root-owned guest `tmux` session:

```bash
printf '%s\n' "${COSIM_GUEST_SUDO_PASSWORD:?set COSIM_GUEST_SUDO_PASSWORD}" | sudo -S bash -lc \
  'tmux kill-session -t cosim-hang 2>/dev/null || true
   tmux new-session -d -s cosim-hang -n reproduce "<exact-target-command>; echo EXIT:\$?; exec bash"
   tmux new-window -t cosim-hang -n debug "cd /mnt && bash"'
printf '%s\n' "${COSIM_GUEST_SUDO_PASSWORD:?set COSIM_GUEST_SUDO_PASSWORD}" | sudo -S tmux attach -t cosim-hang
```

ROCm programs that open `/dev/kfd` or `/dev/dri/renderD*` must run with the
same privilege level as the test runner. A plain-user launch is invalid for
these targets because it can change ROCm device visibility.

## Sample Contents

Inside the debug window:

```bash
pgrep -af <program>
ps -T -p <pid>
cat /proc/<pid>/stack
gdb -batch -p <pid> -ex "thread apply all bt"
```

Capture two bounded samples 10-20 seconds apart. Each sample must include:

- target log tail
- full `dmesg`
- filtered `dmesg` for `amdgpu|kfd|drm|irq|ih|fault|vmid|pasid`
- process id, thread ids, and thread states
- per-thread `wchan`
- per-thread kernel stacks
- file descriptors
- user-space backtrace

Treat the wait state as proven only when both samples show the same live
process, no new target output, the same blocking thread set, compatible wait
channels, and compatible user-space backtraces.

## Shutdown

After evidence is saved, shut down the guest through the console pipe with root
privileges:

```bash
printf '%s\n' "printf '%s\n' \"\${COSIM_GUEST_SUDO_PASSWORD:?set COSIM_GUEST_SUDO_PASSWORD}\" | sudo -S poweroff" > "$CONSOLE_PIPE"
```
