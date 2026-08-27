---
name: cosim-gpu-disk-image-edit
description: 不重建 Guest 时离线挂载并修改当前 raw 磁盘镜像、systemd unit 或 modprobe 配置时使用；要求所有 cosim/QEMU 会话已停止。
---

# cosim-gpu Guest 磁盘镜像编辑

当前镜像是
`gem5-resources/src/x86-ubuntu-gpu-ml/disk-image/x86-ubuntu-rocm70`。
它是约 54.7 GiB 的 raw GPT 镜像：`/dev/sda1` 是 Linux root filesystem，
`/dev/sda2` 是 1 MiB BIOS boot partition。

## 前置检查

raw base 不能与 QEMU 同时使用。先确认没有 launcher、test runner、QEMU 或对应
gem5 container，再确认 libguestfs 工具存在：

```bash
pgrep -af '[q]emu-system-x86_64|[c]osim_launch.sh|[r]un_cosim_tests.sh'
docker ps --filter 'name=gem5-cosim-'
command -v guestmount
command -v guestunmount
```

任一 cosim 会话仍存活时停止，不要挂载。缺少工具时安装 Host 的
`libguestfs-tools` 后再继续。

## 挂载与确认

```bash
DISK="$PWD/gem5-resources/src/x86-ubuntu-gpu-ml/disk-image/x86-ubuntu-rocm70"
MOUNTPOINT="$(mktemp -d /tmp/cosim-disk.XXXXXX)"
fdisk -l "$DISK"
guestmount -a "$DISK" -m /dev/sda1 --rw "$MOUNTPOINT"
mountpoint -q "$MOUNTPOINT"
sed -n '1,20p' "$MOUNTPOINT/etc/os-release"
```

`guestmount` 通过 FUSE 直接写 raw image，不需要同时 loop-mount，也不要让
QEMU 打开该镜像。

## 修改

先检查目标文件，再复制明确的 Host 文件。systemd unit 可放到
`$MOUNTPOINT/etc/systemd/system/`；离线 enable 通过创建
`multi-user.target.wants` 相对 symlink 完成：

```bash
UNIT=cosim-extra.service
install -m 0644 "path/to/${UNIT}" "$MOUNTPOINT/etc/systemd/system/${UNIT}"
mkdir -p "$MOUNTPOINT/etc/systemd/system/multi-user.target.wants"
ln -sfn "../${UNIT}" \
    "$MOUNTPOINT/etc/systemd/system/multi-user.target.wants/${UNIT}"
```

modprobe 配置放到 `$MOUNTPOINT/etc/modprobe.d/*.conf`：

```bash
install -m 0644 path/to/cosim-amdgpu.conf \
    "$MOUNTPOINT/etc/modprobe.d/cosim-amdgpu.conf"
```

当前自动初始化由 `cosim-gpu-setup.service` 和
`/usr/local/bin/cosim-gpu-setup.sh` 完成；修改 unit、脚本或 amdgpu 参数时同时
核对二者，避免与启动时 blacklist 和固定参数冲突。

## 卸载与验证

```bash
sync
guestunmount "$MOUNTPOINT"
if mountpoint -q "$MOUNTPOINT"; then
    echo "镜像仍处于挂载状态" >&2
    exit 1
fi
rmdir "$MOUNTPOINT"
.local/cosim/qemu/10.1.5/bin/qemu-img info --output=json "$DISK"
fdisk -l "$DISK"
```

需要验证文件内容时，再以新的临时目录 `--ro` 挂载并读取目标文件，完成后同样
`guestunmount`。

## 风险

运行时 launcher 通常为 raw base 创建 run-scoped qcow2 overlay；本流程却直接改变
raw base，会使现有 Guest hash、content seal、build metadata 和旧测试结果失效。
修改前记录镜像路径、大小和 SHA-256，并为高风险操作准备空间足够的可恢复副本或
快照。不要在挂载状态下启动 QEMU，也不要把临时 mountpoint 或镜像副本提交到 Git。
