---
name: cosim-gpu-disk-image-edit
description: Use when the guest disk image needs modification without a full rebuild. Mount with guestmount, add files, systemd services, kernel module config.
---

# Cosim Disk Image Edit

Modify the cosim guest disk image without rebuilding.

## Prerequisites

- QEMU must NOT be running (disk image cannot be mounted while in use)
- `guestmount` must be available (`libguestfs-tools` package)

```bash
pgrep -af 'qemu-system-x86_64|cosim_launch.sh|run_cosim_tests.sh' && echo "STOP COSIM FIRST" || echo "OK"
find /tmp -maxdepth 1 -type d -name '*qemu-cosim*.session' -print
which guestmount || echo "Install: apt install libguestfs-tools"
```

## Disk image location

```
gem5-resources/src/x86-ubuntu-gpu-ml/disk-image/x86-ubuntu-rocm70
```

Format: raw, GPT, partition 1 is the root filesystem.

## Mount

```bash
DISK="./gem5-resources/src/x86-ubuntu-gpu-ml/disk-image/x86-ubuntu-rocm70"
MOUNTPOINT="/tmp/cosim-disk"
mkdir -p "$MOUNTPOINT"
guestmount -a "$DISK" -m /dev/sda1 --rw "$MOUNTPOINT"
```

## Edit scope

Supported edit classes:

- Add or replace files under the guest filesystem.
- Add or update systemd units and matching enablement symlinks.
- Add or update kernel module configuration under `/etc/modprobe.d/`.
- Install ROCm runtime debug components through repository scripts when the task is runtime debugging.
- Inspect disk layout with `fdisk -l "$DISK"` if `guestmount` cannot find `/dev/sda1`.

Keep the concrete file contents in the task artifact or source patch. Do not embed large service templates or generated scripts in the skill itself.

## Unmount

Always unmount before starting QEMU:

```bash
guestunmount "$MOUNTPOINT"
sleep 2
ls "$MOUNTPOINT" 2>/dev/null && echo "Still mounted!" || echo "OK"
```

## Notes

- `guestmount` uses FUSE — no sudo needed
- Changes write directly to raw image file
- Back up the disk image before risky changes (55 GB, consider snapshots)
