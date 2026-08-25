# QEMU `error_setv` 重复设置模式

本文用于诊断 QEMU 的 `error_setv` assertion failure。该模式在 2026 年 6 月的
cosim vfio-user 启动调试中发现；新 case 仍需匹配当前 QEMU source provenance。

## 机制

QEMU 的错误报告约定使用 `Error **errp`，即指向 error pointer 的 pointer。设置新
error 时，必须保持 `*errp == NULL`。

```c
// util/error.c:51-62
static void error_setv(Error **errp, ...) {
    if (errp == NULL) {          // L59：调用方不接收错误，直接返回
        return;
    }
    assert(*errp == NULL);       // L62：*errp 已设置时触发 SIGABRT
}
```

关键行为：

- `errp == NULL`：callee 不接收错误详情，`error_setv` 直接返回。
- `errp != NULL && *errp == NULL`：正常路径，设置第一个 error。
- `errp != NULL && *errp != NULL`：在非空 `*errp` 上设置第二个 error，触发 assertion。

## 反模式

把同一个 `Error **errp` 依次传给两个都可能设置错误的函数：

```c
// 反模式（proxy.c:234-241；是否仍存在必须按锁定源码核验）：
ret = qio_channel_readv_full(proxy->ioc, &iov, 1, fdp, numfdp, 0,
                             errp);   // 失败（如 ECONNRESET）时可能设置 *errp
if (ret < 0) {
    error_setg_errno(errp, errno, "failed to read header");
    // 再次设置 *errp，导致 assert(*errp == NULL) 失败
}
```

## 修复模式

使用局部 `Error *` 隔离两次错误设置：

```c
// 修复：
Error *local_err = NULL;
ret = qio_channel_readv_full(proxy->ioc, &iov, 1, fdp, numfdp, 0,
                             &local_err);  // 隔离内部 error
if (ret < 0) {
    error_propagate_prepend(errp, local_err, "failed to read header: ");
    // local_err 的所有权被转移，*errp 只设置一次
}
```

`error_propagate_prepend` 把 `local_err` 的所有权连同 prefix message 转移到
`*errp`，因此 `*errp` 只设置一次。

## 识别模式

在锁定的 QEMU 源码中查找：某函数调用接收 `errp` 后，后续路径又对同一 `errp`
调用 error-set：

```bash
# 查找 QEMU 中可能的重复设置位置
search pattern="error_set[gv]_errno\(errp" paths=["qemu/"]
# 交叉检查：紧邻的前一次调用是否也接收 errp？
```

额外风险：锁定的 QEMU 10.1.5 中，`error_report_err(Error *err)` 已经报告并释放
传入的 `Error`；调用方不得再次执行 `error_free(err)`，否则会 double-free。由于参数
按值传递，调用方变量不会自动清零；若同一局部变量还要复用，必须显式置空：

```c
error_report_err(local_err);
local_err = NULL;
```

也可以使用一次性的 ownership-transfer API。无论选择哪种路径，必须保证 error
object 只消费一次，下一次传入 `&local_err` 前保持 `local_err == NULL`。

## Crash 链：gem5 → QEMU 次生故障

常见 cosim trigger 是 gem5 crash 后 socket 关闭，QEMU 收到 `ECONNRESET`，继而
进入重复设置路径：

```
gem5 assert/crash → socket disconnect
  → QEMU vfio_user_recv_hdr sees ECONNRESET
  → qio_channel_readv_full sets *errp (ECONNRESET)
  → error_setg_errno tries to set *errp again → SIGABRT
```

QEMU 重复设置属于 defensive bug：正常运行不应触发，即使触发也不应使 QEMU crash。
在上面的时序中，gem5 crash 是第一个失败组件，QEMU crash 是掩盖它的次生效应。

## GDB 调试边界

先通过同一 run 的 gem5/QEMU log 与 `--qemu-trace 'vfio_user_*'` 判断 QEMU 是否
真的先失败。需要 GDB 时不得手写或复制 raw QEMU 参数，因为那会绕过 launcher 的
run-scoped socket、shared memory、overlay、manifest 与 cleanup contract。优先分析
该次运行保留的 core；若必须交互 breakpoint，应在已确认的调试计划内为标准 launcher
增加有界的 GDB 入口，并由同一 manifest 管理进程。

用于定位 assertion 的 breakpoint 条件是：

```gdb
# 只在 assertion 确实会触发时停止
break error.c:62 if *errp != NULL
bt full
```

## 源码版本状态

历史发现位置为 `hw/vfio-user/proxy.c:234-241`。不得根据 `master` 的移动状态判断
当前环境；必须先记录 lockfile、QEMU commit/source hash 与 binary provenance，再确认
当前源码是否仍存在相同 caller/callee contract。

## 交叉参考

- `qemu-first-failure.md`：QEMU-first failure 检查。
- `../gem5-model/vmid-assert-lesson.md`：cosim 中 gem5
  `assert(queue_vmid)` 导致 QEMU 重复错误 crash 的具体案例。
- `../analysis/debug-analysis.md`：gem5 与 QEMU 的 first-failure 对比。
