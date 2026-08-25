---
name: cosim-gpu-guest
description: 在运行中的 cosim Guest 内发送命令时使用，包括 console pipe 交互、9p 挂载、GPU 状态检查与安全关机；不用于原地修复部分初始化的驱动。
---

# Cosim Guest 交互

通过 `scripts/run_cosim_tests.sh --keep-alive` 留下的 detached runner 会话与
cosim Guest Linux 交互。

Guest 交互属于诊断路径，通常使用默认 `COSIM_STRICT_ACCEPTANCE=0`。即使调用者把该
变量设为 `1`，`--keep-alive` 仍因 mode 与 cleanup 不满足要求而不能成为 strict v2
accepted row。完成诊断后不得改标当前 artifact；修复并提交后，应在两个 clean tree
上由 `cosim-gpu-test` 显式以 `COSIM_STRICT_ACCEPTANCE=1` 启动新的非 keep-alive row。

## 前提

Runner 保留调试环境时会输出当前 console log 和 control pipe：

```text
Console log: /path/to/qemu.log
Console pipe: /tmp/<session-name>-<run-id>.session/console.in
```

若终端输出不可用，必须从本次计划记录的 artifact 路径读取 console log；control pipe
只能根据同一次 `--keep-alive` 会话的 run ID 与 session name 推导。不得猜测旧的
`/tmp/<session>-<run>.log` 路径：

```bash
SESSION_NAME=qemu-cosim-tests
RUN_ID=replace-with-run-id
ARTIFACT_DIR=artifacts/task/row
CONSOLE_PIPE="/tmp/${SESSION_NAME}-${RUN_ID}.session/console.in"
CONSOLE_LOG="${ARTIFACT_DIR}/qemu.log"
MANIFEST="/tmp/cosim-${RUN_ID}.session/resources.manifest"
LAUNCH_PID_FILE="/tmp/${SESSION_NAME}-${RUN_ID}.session/launcher.pid"
test -f "$LAUNCH_PID_FILE"
LAUNCH_PID="$(cat "$LAUNCH_PID_FILE")"
[[ "$LAUNCH_PID" =~ ^[1-9][0-9]*$ ]]
kill -0 "$LAUNCH_PID"
tr '\0' ' ' < "/proc/${LAUNCH_PID}/cmdline" | grep -F -- "$ARTIFACT_DIR"
CONTAINER_COUNT="$(docker ps -q \
    --filter "label=io.cosim-gpu.run-id=${RUN_ID}" | wc -l)"
[[ "$CONTAINER_COUNT" -eq 1 ]]
docker ps --filter "label=io.cosim-gpu.run-id=${RUN_ID}" \
    --format '{{.Names}}: {{.Status}}'
test -p "$CONSOLE_PIPE"
test -f "$CONSOLE_LOG"
test -f "$MANIFEST"
test -f "${ARTIFACT_DIR}/runner-invocation.txt"
test -f "${ARTIFACT_DIR}/patch/source-snapshot.txt"
grep -F 'strict_acceptance=' "${ARTIFACT_DIR}/runner-invocation.txt"
if [[ -f "${ARTIFACT_DIR}/runner-metadata.txt" ]]; then
    echo "workload 已产生 completion metadata"
else
    echo "pre-completion 调试会话：不能要求 runner-metadata.txt"
fi
```

## 发送命令

```bash
send_console() {
    local command="$1"
    timeout 5s bash -c 'printf "%s\n" "$1" > "$2"' \
        cosim-console "$command" "$CONSOLE_PIPE"
}
send_console '<command>'
timeout 5s bash -c 'printf "\003" > "$1"' cosim-console "$CONSOLE_PIPE"
tail -n 80 "$CONSOLE_LOG"
```

有界等待模式：
```bash
baseline=$(wc -l < "$CONSOLE_LOG")
send_console '<command>'
deadline=$((SECONDS + 30))
current=$baseline
while (( SECONDS < deadline )); do
    current=$(wc -l < "$CONSOLE_LOG")
    if [[ "$current" -gt "$((baseline + 3))" ]]; then
        tail -20 "$CONSOLE_LOG"
        break
    fi
    sleep 2
done
[[ "$current" -gt "$((baseline + 3))" ]]
```

## 常用操作

### 挂载 9p share

```bash
send_console 'mount -t 9p -o trans=virtio,version=9p2000.L cosim_share /mnt'
```

挂载后，`/mnt` 下的路径必须解析到共享树内部。若 Host symlink 指向共享仓库之外，
Guest 虽可看到 symlink，却可能把目标解析到未挂载路径。若脚本或 artifact 因此缺失，
应分类为 Guest bridge path 问题；改用 Guest 可见路径重跑后，结果才能作为模型证据。

不得假设 Host 上的 artifact 存储布局。开发者可能使用真实目录、指向大容量磁盘的
symlink，CI 也可能使用临时 workspace。因此 Guest 内执行的 runner 脚本应使用共享树
内部可配置的 Guest 可见 bridge path；最终 log、matrix、verdict 与 provenance 仍归档
到请求的 artifact 目录。临时 bridge 目录本身不是证据，清理前必须把脚本与输出保存
到对应 row artifact。

### 构建并运行测试

```bash
# Runner 把暂存测试树直接共享为 /mnt。
send_console 'cd /mnt && make -j1'

# Guest serial-getty 自动登录 root，命令不读取或保存密码。
send_console 'cd /mnt && ./build/vector_add'
```

当前 Guest console contract 是 root auto-login；访问 `/dev/kfd` 或
`/dev/dri/renderD*` 时不得要求用户输入密码，也不得把密码写入环境变量、脚本或
artifact。若当前会话不是 root，应将其分类为 Guest image/setup contract 失败，保留
证据并通过固定构建输入修复，不能在调试命令中注入凭据。

### 检查 GPU 状态

```bash
send_console 'rocm-smi'
send_console 'rocminfo 2>/dev/null | head -40'
send_console 'dmesg | grep -i amdgpu | tail -10'
```

### Driver 或 setup service 失败

不要手工写 `/dev/mem`，也不要在部分 `hw_init` 后执行 `rmmod/modprobe amdgpu`。
应先采集 `systemctl status cosim-gpu-setup.service`、`journalctl -u
cosim-gpu-setup.service`、PCI binding、module state 与第一条 kernel failure；随后保存
artifact，使用该次运行的 manifest 清理，并在修复固定 Guest 构建输入后启动全新会话。
原地修复过的 Guest 不能成为 acceptance evidence。
默认 dirty replay 或 live Guest 采样也不能进入 strict v2 matrix；最终 accepted row
必须另行证明 `strict_acceptance=1`、两个 clean tree 与 tracked baseline lock。

### 关机

```bash
send_console 'poweroff'
```

保存 hang-debug 证据与 manifest 快照后再关机，避免遗留等待人工处理的 Guest。等待
launcher 退出并验证 artifact 中的 `cleanup-status.txt`。若 Guest 已无法接受 console
输入，记录该事实；先验证并终止本次 run 的 launcher process group，确认其退出后才
能用仍存在的精确 manifest 执行 fallback cleanup。不得对 live launcher 直接运行
cleanup，也不得执行无范围清理。具体范式见
`cosim-gpu-debug/references/analysis/live-wait-state.md`。

## 与其他技能的关系

- 自动测试执行使用 `cosim-gpu-test`。
- Host/Guest 双侧定位使用 `cosim-gpu-debug`。
- 手动诊断会话只发送本文件列出的只读或有界命令，并保存输出。
