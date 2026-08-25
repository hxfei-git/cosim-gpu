# Live wait-state 采样

目标进程仍存活但不再推进时使用本文。目标是保留阻塞对象，取得两个可比较的状态
样本，并在诊断结束后证明本次 run 的资源已经清理。该证据只用于定位，不能作为
operator acceptance row。

## 启动规则

使用 `cosim-gpu-test` 已确认的精确 operator identity，并让 runner 在可控 PTY 中启动
诊断会话：

```bash
CASE_ID=wait-vector-add-001
CASE_ARTIFACT="artifacts/debug/${CASE_ID}"
COSIM_STRICT_ACCEPTANCE=0 COSIM_RUN_ID="$CASE_ID" \
    ./scripts/run_cosim_tests.sh --keep-alive \
    --output-dir "$CASE_ARTIFACT" vector_add
```

该命令显式创建 diagnostic row；不得把它事后改标为 strict v2 row。修复并提交后，只有
在顶层仓库和 `gem5/` 都 clean、tracked baseline lock 等于 `HEAD` 时，才能另行以
`COSIM_STRICT_ACCEPTANCE=1` 启动 fresh 非 keep-alive row 作为最终 accepted 候选。

若 workload 正常完成，`--keep-alive` 会保留 QEMU/gem5，并因尚未完成 cleanup 而返回
非零。若已知 wait 在 runner 完成前出现，先用两个有界观察窗口确认没有进展，再向
Host runner 发送一次 `SIGINT`；其 interrupt handler 会保留同一会话。不要用会同时
终止 launcher/QEMU 的通用 timeout wrapper，也不要把临时 verdict 当作 PASS。

Runner 会打印精确的 console log 和 pipe。终端输出不可用时，只能从本次 run identity
推导并验证：

```bash
SESSION_NAME=qemu-cosim-tests
RUN_ID="$CASE_ID"
ARTIFACT_DIR="$(realpath -m -- "$CASE_ARTIFACT")"
CONTROL_DIR="/tmp/${SESSION_NAME}-${RUN_ID}.session"
CONSOLE_PIPE="${CONTROL_DIR}/console.in"
CONSOLE_LOG="${ARTIFACT_DIR}/qemu.log"
MANIFEST="/tmp/cosim-${RUN_ID}.session/resources.manifest"
LAUNCH_PID_FILE="${CONTROL_DIR}/launcher.pid"

test -p "$CONSOLE_PIPE"
test -f "$CONSOLE_LOG"
test -f "$MANIFEST"
test -f "$LAUNCH_PID_FILE"
test -f "${ARTIFACT_DIR}/patch/source-snapshot.txt"
```

`runner-metadata.txt` 只会在 workload 得到 completion token 后生成；SIGINT 保留的
wait-state 会话不能把它作为前置条件。对正常完成后由 `--keep-alive` 保留的会话，
该文件应存在，但仍因 cleanup 未完成而不能作为 acceptance evidence。

## Guest 会话

当前 Guest serial-getty 自动登录 root；console 命令不得读取、传递或保存密码。Runner
把暂存测试树直接共享为 `/mnt`，所以本次精确 binary 是 `/mnt/build/<program>`，而
不是 Host repository 布局下的 `/mnt/tests/build/<program>`。传输规则见
[cosim-gpu-guest](../../../cosim-gpu-guest/SKILL.md)。

目标已由 runner 启动时，先读取 PID，不要再次启动第二份进程：

```bash
send_console() {
    local command="$1"
    timeout 5s bash -c 'printf "%s\n" "$1" > "$2"' \
        cosim-console "$command" "$CONSOLE_PIPE"
}
send_console 'pgrep -af vector_add; ps -eLo pid,tid,stat,wchan:32,comm,args | grep vector_add'
```

只有需要在已保留的 fresh Guest 中重放同一 binary 时，才创建 root-owned `tmux`
会话；先验证工具存在：

```bash
send_console 'command -v tmux && test -x /mnt/build/vector_add'
send_console \
    'tmux kill-session -t cosim-wait 2>/dev/null || true; tmux new-session -d -s cosim-wait -n reproduce "cd /mnt && ./build/vector_add; echo EXIT:\$?; exec bash"'
```

若 `tmux` 或精确 binary 不存在，将其分类为 Guest image、staging 或 invocation failure；
不要临时安装软件，也不要换用另一份未记录 provenance 的 binary。

## 两次有界采样

对同一 PID 采样两次，间隔 10–20 秒。每次至少包含：

- target log tail；
- 完整 `dmesg` 与 `amdgpu|kfd|drm|irq|ih|fault|vmid|pasid` 过滤结果；
- PID、TID、thread state、每线程 `wchan` 与 kernel stack；
- file descriptor；
- 若 Guest 已包含 GDB，在非侵入样本完成后再采集一次 user-space backtrace。

通过 console pipe 发送带唯一 token 的有界命令，并从 `qemu.log` 中保存两个独立
window。以下函数同时保存 target console tail、完整/过滤 `dmesg`、线程 wait channel、
kernel stack 与 FD；Guest 命令本身由 `timeout` 限制为 45 秒，Host marker 等待限制为
60 秒。只有 Guest wrapper 明确返回 `0` 才允许开始下一次样本：

```bash
WAIT_EVIDENCE_DIR="${ARTIFACT_DIR}/wait-samples"
mkdir -p "$WAIT_EVIDENCE_DIR"
capture_wait_sample() {
    local sample="$1"
    local baseline current deadline
    baseline=$(wc -l < "$CONSOLE_LOG")
    tail -n 160 "$CONSOLE_LOG" > \
        "${WAIT_EVIDENCE_DIR}/sample-${sample}-target-before.log"
    send_console "timeout --kill-after=5s 45s bash -c 'pid=\$(pgrep -n vector_add) || exit 2; echo __WAIT_SAMPLE_BEGIN__:${sample}:\${pid}; ps -T -p \"\$pid\"; for task in /proc/\"\$pid\"/task/*; do printf \"%s \" \"\$task\"; cat \"\$task/wchan\"; cat \"\$task/stack\"; done; ls -l /proc/\"\$pid\"/fd; echo __DMESG_FULL__:${sample}; dmesg; echo __DMESG_FILTERED__:${sample}; dmesg | grep -Ei \"amdgpu|kfd|drm|irq|ih|fault|vmid|pasid\"'; sample_rc=\$?; echo __WAIT_SAMPLE_END__:${sample}:\${sample_rc}"
    deadline=$((SECONDS + 60))
    current=$baseline
    while (( SECONDS < deadline )); do
        current=$(wc -l < "$CONSOLE_LOG")
        if tail -n "+$((baseline + 1))" "$CONSOLE_LOG" | \
                grep -q "__WAIT_SAMPLE_END__:${sample}:"; then
            break
        fi
        sleep 2
    done
    tail -n "+$((baseline + 1))" "$CONSOLE_LOG" > \
        "${WAIT_EVIDENCE_DIR}/sample-${sample}-guest-window.log"
    tail -n 160 "$CONSOLE_LOG" > \
        "${WAIT_EVIDENCE_DIR}/sample-${sample}-target-after.log"
    grep -q "__WAIT_SAMPLE_END__:${sample}:0" \
        "${WAIT_EVIDENCE_DIR}/sample-${sample}-guest-window.log"
}
if ! capture_wait_sample 1; then
    echo "sample 1 未在 Guest/Host 双重 deadline 内成功，停止后续采样" >&2
    exit 1
fi
sleep 15
if ! capture_wait_sample 2; then
    echo "sample 2 未在 Guest/Host 双重 deadline 内成功" >&2
    exit 1
fi
```

只有两次样本显示相同 live process、没有新 target output、相同 blocking thread set、
兼容的 wait channel 和兼容的 user-space backtrace 时，才能确认 wait state。若 dispatch
或 completion counter 仍变化，应先分类为 throughput/timeout-budget，不要改模型。

## 结束与清理

先把 console window、完整 log 和仍存在的 manifest 快照保存到该 run artifact。优先
通过 root console 执行 `poweroff`，等待 launcher 退出并检查
`cleanup-status.txt`。如果 Guest 不再响应，必须先核对 `launcher.pid`、process group
和 artifact-dir 参数属于本次 run，再终止该 launcher process group；只有确认该组已
退出、manifest 仍存在时，才调用 manifest-scoped fallback cleanup。不得对 live
launcher 直接运行 cleanup，也不得执行无范围删除。

完整的有界终止、manifest 快照、fallback 与验证范式见
[Lab 2](../../../../../docs/zh/labs.md#lab-amdgpu-kfd-init)。完成条件是诊断证据已归档、
launcher process group 不存在、manifest 中列出的 runtime 资源不存在，并且本次
cleanup 状态为 `PASS`。
