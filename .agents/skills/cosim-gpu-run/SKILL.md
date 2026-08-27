---
name: cosim-gpu-run
description: 启动或清理 QEMU+gem5 会话，以及通过串口、console FIFO 或 9p 与运行中的 Guest 交互时使用；不用于自动执行算子测试。
---

# cosim-gpu 运行与 Guest 交互

## 启动

先做只读运行检查，再使用 launcher。gem5 在 Docker 中运行，Host 上的锁定 QEMU
使用 Q35+KVM，并通过 run-scoped vfio-user Unix socket 连接。

```bash
./scripts/cosim_preflight.sh run
./scripts/cosim_launch.sh
```

需要固定 artifact、9p share 或定向 trace 时：

```bash
COSIM_RUN_ID=manual-001 ./scripts/cosim_launch.sh \
    --artifact-dir artifacts/standalone/manual-001 \
    --share-dir "$PWD" \
    --gem5-debug MI300XCosim,AMDGPUDevice \
    --qemu-trace 'vfio_user_*'
```

`--gem5-debug` 接受逗号分隔的 gem5 debug flags；`--qemu-trace` 接受 QEMU
trace event expression。其他当前参数以 `./scripts/cosim_launch.sh --help` 为准。
默认 artifact 是 `artifacts/standalone/<run-id>/`，其中至少包含
`gem5.log`、`launch-invocation.txt`、preflight 结果及退出后的
`cleanup-status.txt`。standalone QEMU 串口在当前终端。

## Console FIFO 与日志

`run_cosim_tests.sh --keep-alive` 会打印本次会话的 `Console log` 与
`Console pipe`。只能使用这两个实际输出，不要猜旧路径。向 FIFO 写命令前确认
launcher PID 仍存活：

```bash
SESSION_NAME=qemu-cosim-tests
RUN_ID=replace-with-run-id
ARTIFACT_DIR=artifacts/vector_add/replace-with-run-id
CONSOLE_PIPE="/tmp/${SESSION_NAME}-${RUN_ID}.session/console.in"
LAUNCH_PID_FILE="/tmp/${SESSION_NAME}-${RUN_ID}.session/launcher.pid"
test -p "$CONSOLE_PIPE"
test -r "$LAUNCH_PID_FILE"
LAUNCH_PID="$(sed -n '1p' "$LAUNCH_PID_FILE")"
kill -0 "$LAUNCH_PID"
timeout 5s bash -c 'printf "%s\n" "$1" > "$2"' \
    cosim-console 'rocm-smi' "$CONSOLE_PIPE"
tail -n 80 "${ARTIFACT_DIR}/qemu.log"
```

## Guest 常用操作

launcher 使用 `--share-dir` 后，在 Guest 中挂载：

```bash
mount -t 9p -o trans=virtio,version=9p2000.L cosim_share /mnt
```

检查初始化和 GPU 可见性：

```bash
systemctl status cosim-gpu-setup.service --no-pager
journalctl -u cosim-gpu-setup.service -b --no-pager
lspci -nn -d 1002:
lsmod | grep amdgpu
rocm-smi
rocminfo
dmesg | grep -iE 'amdgpu|kfd' | tail -n 80
```

Guest 正常由 `cosim-gpu-setup.service` 发布 ROM/discovery 数据并加载
`amdgpu`。若要在尚未加载驱动、且 service 从未运行的全新 Guest 中手动触发
加载，使用 `systemctl start cosim-gpu-setup.service`；它会采用仓库固定参数
`ip_block_mask=0x67 ppfeaturemask=0 dpm=0 audio=0 ras_enable=0 discovery=2`。
service 已失败或驱动只完成部分 `hw_init` 时，不要再次启动 service、卸载/重载
模块或手写 `/dev/mem`；保存日志，结束该会话后从全新 Guest 重试。

## 关机与残留资源

优先在 Guest 执行 `poweroff`，等待 launcher 退出并检查
`cleanup-status.txt`。standalone 终端也可用 QEMU 的 `Ctrl-A X` 退出，EXIT trap
仍会执行 manifest cleanup。

残留资源先只读盘点，再按精确 run manifest 清理：

```bash
./scripts/cosim_launch.sh --force-clean
RUN_ID=replace-with-run-id
./scripts/cosim_cleanup.sh --run-id "$RUN_ID"
./scripts/cosim_cleanup.sh --run-id "$RUN_ID" \
    --manifest "/tmp/cosim-${RUN_ID}.session/resources.manifest" --confirm
```

第一条 cleanup 命令是 dry-run；确认清单只属于目标 run 后才执行带
`--confirm` 的命令。不得对仍存活的 launcher 直接 cleanup，也不得按宽泛进程名、
socket glob 或整个 `/dev/shm` 做清理。
