#!/usr/bin/env python3
"""提供 cosim 日志时间戳、致命错误和 GPU 执行证据的共享解析。"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import os
import re
import shlex
import stat
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple


NANOSECONDS_PER_SECOND = 1_000_000_000
BOUNDARY_HANDSHAKE_MIN_TIMEOUT_SECONDS = 30
REQUIRED_STRICT_DEBUG_FLAGS = (
    "HSAPacketProcessor",
    "GPUCommandProc",
    "GPUDisp",
    "GPUKernelInfo",
)
GPU_EVIDENCE_MAGIC = "COSIM_GPU_EVIDENCE_V1"
GPU_EVIDENCE_FILENAME = "gem5-evidence.tsv"
GPU_EVIDENCE_HEADER = (
    "schema\trun_id\tseq\ttick\tevent\tgpu\tdispatch\twg\tcu"
)

_RFC3339NANO_RE = re.compile(
    r"^(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})T"
    r"(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2}):(?P<second>[0-9]{2})"
    r"(?:\.(?P<fraction>[0-9]{1,9}))?"
    r"(?P<zone>Z|[+-][0-9]{2}:[0-9]{2})$"
)
_DOCKER_TIMESTAMP_RE = re.compile(
    r"^(?P<timestamp>[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?"
    r"(?:Z|[+-][0-9]{2}:[0-9]{2})) (?P<payload>.*)$"
)
_GEM5_FATAL_RE = re.compile(
    r"^(?:(?:gem5\s+)|(?:[^:\r\n]+:[0-9]+:\s+))?"
    r"(?:panic|fatal):(?:\s|$)"
)
_ASSERTION_RE = re.compile(r"(?:^|:\s+)Assertion\b.*\bfailed\.?$")
_GEM5_ABORT_RE = re.compile(r"^Program aborted(?: at tick [0-9]+)?$")
_GEM5_SEGFAULT_RE = re.compile(
    r"^gem5 has encountered a segmentation fault!$"
)
_SHELL_CRASH_RE = re.compile(
    r"^(?:(?:[^:\r\n ]*/)?(?:qemu-system-[A-Za-z0-9_.-]+|"
    r"gem5(?:\.(?:opt|fast|debug))?): )?"
    r"(?P<kind>Segmentation fault|Aborted) \(core dumped\)$"
)
_BASH_PROCESS_CRASH_RE = re.compile(
    r"^(?P<script>[^:\r\n]+): line (?P<line>[1-9][0-9]*): "
    r"(?P<pid>[1-9][0-9]*)[ \t]+"
    r"(?P<kind>Segmentation fault|Aborted)[ \t]+\(core dumped\)"
    r"(?:[ \t]+.+)?$"
)
_QEMU_SIGNAL_RE = re.compile(
    r"(?:^|#\s+)qemu-system-[^:\s]+: terminating on signal "
    r"(?P<signal>[0-9]{1,3})(?:\s|$)"
)
_READLINE_BRACKETED_PASTE_END_PREFIX = "\x1b[?2004l\r"
_COMMAND_RE = re.compile(r"^command line: (?P<command>.+)$")
_RUN_MARKER_RE = re.compile(
    r"^  Run-ID:     (?P<run_id>[A-Za-z0-9][A-Za-z0-9._-]{0,127})$"
)
_HSA_RE = re.compile(r"^\[COSIM_ENV\] HSA_ENABLE_INTERRUPT=(?P<value>[01])$")
_TEST_TIMEOUT_POLICY_RE = re.compile(
    r"^\[COSIM_TIMEOUT\] TEST_TIMEOUT_SECS=(?P<value>[1-9][0-9]*)$"
)
_TIMEOUT_SIGNAL_RE = re.compile(
    r"^\[(?:COSIM_)?(?:BOOT_|GEM5_INIT_|TEST_)?TIMEOUT\](?:\s|$)"
)
_SIMULATOR_EXIT_SIGNAL_RE = re.compile(
    r"^\[COSIM_(?:GEM5|QEMU|SIMULATOR|LAUNCHER)_EXIT\](?:\s|$)"
)
_COMPLETION_TOKEN_RE = re.compile(
    r"^__(?P<role>COSIM_(?:COMPILE|TEST)_DONE)_"
    r"(?P<program>[a-z0-9_]{1,128})_(?P<run_sha256>[0-9a-f]{64})__:"
    r"(?P<status>[0-9]{1,10})$"
)
_BOUNDARY_READY_RE = re.compile(
    r"^__COSIM_BOUNDARY_READY_"
    r"(?P<program>[a-z0-9_]{1,128})_(?P<run_sha256>[0-9a-f]{64})__:"
    r"(?P<sha256>[0-9a-f]{64})$"
)
_PASS_RE = re.compile(r"^\[PASS\] (?P<program>[a-z0-9_]{1,128})$")
_FAIL_RE = re.compile(r"^\[FAIL\](?:\s|$)")
_PROGRAM_RE = re.compile(r"^[a-z0-9_]{1,128}$")
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_POSITIVE_INTEGER_RE = re.compile(r"^[1-9][0-9]*$")
_NONNEGATIVE_INTEGER_RE = re.compile(r"^(?:0|[1-9][0-9]{0,19})$")
_EVIDENCE_INTEGER_RE = re.compile(r"^(?:-1|0|[1-9][0-9]{0,19})$")
_GPU_EVIDENCE_FIELDS: Mapping[str, Tuple[bool, bool, bool, bool]] = {
    "session_start": (False, False, False, False),
    "client_connected": (True, False, False, False),
    "test_begin": (True, False, False, False),
    "test_end": (True, False, False, False),
    "kernel_launch": (True, True, False, False),
    "workgroup_dispatch": (True, True, True, True),
    "workgroup_complete": (True, True, True, False),
    "kernel_complete": (True, True, False, False),
}


@dataclass(frozen=True)
class StableLogSnapshot:
    """一次稳定、拒绝符号链接的日志快照。"""

    sha256: str
    stat_identity: Tuple[int, int, int, int, int, int]
    line_count: int


def _stat_identity(value: os.stat_result) -> Tuple[int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def read_stable_log_snapshot(
    path: Path,
    line_consumer: Optional[Callable[[int, bytes], None]] = None,
) -> StableLogSnapshot:
    """用单个 ``O_NOFOLLOW`` 文件描述符流式读取、解析并哈希日志。"""

    if not hasattr(os, "O_NOFOLLOW"):
        raise OSError("当前平台不支持 O_NOFOLLOW")
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise OSError("日志不是普通文件")
        digest = hashlib.sha256()
        byte_count = 0
        line_count = 0
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            for line_count, raw_line in enumerate(handle, 1):
                byte_count += len(raw_line)
                digest.update(raw_line)
                if line_consumer is not None:
                    line_consumer(line_count, raw_line)
        after = os.fstat(descriptor)
        before_identity = _stat_identity(before)
        after_identity = _stat_identity(after)
        if before_identity != after_identity:
            raise OSError("日志在读取期间发生变化")
        path_identity = _stat_identity(os.stat(path, follow_symlinks=False))
        if after_identity != path_identity:
            raise OSError("日志路径在读取期间被替换")
        if byte_count != before.st_size:
            raise OSError("日志读取长度与稳定文件大小不一致")
        return StableLogSnapshot(digest.hexdigest(), before_identity, line_count)
    finally:
        os.close(descriptor)


def stable_log_sha256(path: Path) -> str:
    """返回稳定普通日志快照的 SHA-256。"""

    return read_stable_log_snapshot(path).sha256


def completion_token_run_sha256(run_id: str) -> str:
    """生成绑定 run ID 的 completion token 后缀。"""

    return hashlib.sha256(run_id.encode("utf-8", errors="strict")).hexdigest()


def evidence_boundary_token(run_id: str, program: str) -> str:
    """生成绑定 run/program 的 128-bit AQL boundary token。"""

    if _RUN_ID_RE.fullmatch(run_id) is None or ".." in run_id:
        raise ValueError(f"无效 run ID：{run_id!r}")
    if _PROGRAM_RE.fullmatch(program) is None:
        raise ValueError(f"无效 program：{program!r}")
    payload = run_id.encode("ascii") + b"\0" + program.encode("ascii")
    return hashlib.sha256(payload).hexdigest()[:32]


def render_guest_run_script(
    *,
    program: str,
    run_id: str,
    hsa_enable_interrupt: str,
    test_timeout: str,
) -> str:
    """生成 producer 与 verifier 共用的 canonical Guest 执行脚本。"""

    if _PROGRAM_RE.fullmatch(program) is None:
        raise ValueError(f"无效 program：{program!r}")
    if _RUN_ID_RE.fullmatch(run_id) is None or ".." in run_id:
        raise ValueError(f"无效 run ID：{run_id!r}")
    if hsa_enable_interrupt not in ("0", "1"):
        raise ValueError(
            f"无效 HSA_ENABLE_INTERRUPT：{hsa_enable_interrupt!r}"
        )
    if _POSITIVE_INTEGER_RE.fullmatch(test_timeout) is None:
        raise ValueError(f"无效 TEST_TIMEOUT_SECS：{test_timeout!r}")

    run_sha256 = completion_token_run_sha256(run_id)
    boundary_token = evidence_boundary_token(run_id, program)
    boundary_handshake_timeout = max(
        int(test_timeout), BOUNDARY_HANDSHAKE_MIN_TIMEOUT_SECONDS
    )
    compile_token = f"COSIM_COMPILE_DONE_{program}_{run_sha256}"
    boundary_ready_token = f"COSIM_BOUNDARY_READY_{program}_{run_sha256}"
    test_token = f"COSIM_TEST_DONE_{program}_{run_sha256}"
    return (
        "#!/bin/bash\n"
        "set -uo pipefail\n"
        "\n"
        f'export HSA_ENABLE_INTERRUPT="{hsa_enable_interrupt}"\n'
        'case "$HSA_ENABLE_INTERRUPT" in\n'
        "    0|1) ;;\n"
        '    *) echo "invalid HSA_ENABLE_INTERRUPT=$HSA_ENABLE_INTERRUPT"; '
        "exit 2 ;;\n"
        "esac\n"
        'echo "[COSIM_ENV] HSA_ENABLE_INTERRUPT=$HSA_ENABLE_INTERRUPT"\n'
        f'echo "[COSIM_TIMEOUT] TEST_TIMEOUT_SECS={test_timeout}"\n'
        "\n"
        "if ! mountpoint -q /mnt; then\n"
        "    mount -t 9p -o trans=virtio,version=9p2000.L cosim_share /mnt\n"
        "fi\n"
        "\n"
        "cd /mnt || exit 2\n"
        "make -j1\n"
        "build_rc=$?\n"
        f'echo "__{compile_token}__:${{build_rc}}"\n'
        'if [[ "$build_rc" -ne 0 ]]; then\n'
        f'    echo "__{test_token}__:${{build_rc}}"\n'
        '    exit "$build_rc"\n'
        "fi\n"
        'boundary_tool="./tools-build/cosim_evidence_boundary"\n'
        f'boundary_ack="./.cosim_evidence_boundary_ack.{run_sha256}"\n'
        'if [[ -L "$boundary_tool" || ! -f "$boundary_tool" || '
        '! -x "$boundary_tool" ]]; then\n'
        f'    echo "__{test_token}__:125"\n'
        '    exit 125\n'
        'fi\n'
        'boundary_realpath="$(readlink -f -- "$boundary_tool")"\n'
        'if [[ "$boundary_realpath" != '
        '"/mnt/tools-build/cosim_evidence_boundary" ]]; then\n'
        f'    echo "__{test_token}__:125"\n'
        '    exit 125\n'
        'fi\n'
        'boundary_sha256="$(sha256sum -- "$boundary_tool" | '
        'awk \'{print $1}\')"\n'
        'if [[ ! "$boundary_sha256" =~ ^[0-9a-f]{64}$ ]]; then\n'
        f'    echo "__{test_token}__:125"\n'
        '    exit 125\n'
        'fi\n'
        'if [[ -e "$boundary_ack" || -L "$boundary_ack" ]]; then\n'
        f'    echo "__{test_token}__:125"\n'
        '    exit 125\n'
        'fi\n'
        f'echo "__{boundary_ready_token}__:${{boundary_sha256}}"\n'
        'boundary_ack_ready=0\n'
        f'boundary_handshake_timeout_secs={boundary_handshake_timeout}\n'
        'for ((boundary_wait=0; '
        'boundary_wait<boundary_handshake_timeout_secs; '
        'boundary_wait++)); do\n'
        '    if [[ -e "$boundary_ack" || -L "$boundary_ack" ]]; then\n'
        '        boundary_ack_ready=1\n'
        '        break\n'
        '    fi\n'
        '    sleep 1\n'
        'done\n'
        'if [[ -e "$boundary_ack" || -L "$boundary_ack" ]]; then\n'
        '    boundary_ack_ready=1\n'
        'fi\n'
        'if [[ "$boundary_ack_ready" -ne 1 || -L "$boundary_ack" || '
        '! -f "$boundary_ack" ]]; then\n'
        f'    echo "__{test_token}__:124"\n'
        '    exit 124\n'
        'fi\n'
        'boundary_ack_sha256="$(sed -n \'1p\' "$boundary_ack")"\n'
        'boundary_ack_lines="$(wc -l < "$boundary_ack")"\n'
        'rm -f -- "$boundary_ack"\n'
        'if [[ "$boundary_ack_lines" -ne 1 || '
        '"$boundary_ack_sha256" != "$boundary_sha256" ]]; then\n'
        f'    echo "__{test_token}__:125"\n'
        '    exit 125\n'
        'fi\n'
        'boundary_pre_begin_sha256="$(sha256sum -- "$boundary_tool" | '
        'awk \'{print $1}\')"\n'
        'if [[ -L "$boundary_tool" || '
        '"$(readlink -f -- "$boundary_tool")" != '
        '"/mnt/tools-build/cosim_evidence_boundary" || '
        '"$boundary_pre_begin_sha256" != '
        '"$boundary_sha256" ]]; then\n'
        f'    echo "__{test_token}__:125"\n'
        '    exit 125\n'
        'fi\n'
        f'boundary_token="{boundary_token}"\n'
        'timeout --signal=TERM "${boundary_handshake_timeout_secs}s" '
        '"$boundary_tool" '
        'begin "$boundary_token"\n'
        "boundary_rc=$?\n"
        'if [[ "$boundary_rc" -ne 0 ]]; then\n'
        f'    echo "__{test_token}__:${{boundary_rc}}"\n'
        '    exit "$boundary_rc"\n'
        "fi\n"
        "\n"
        f"TEST_TIMEOUT_SECS={test_timeout} ./run_tests.sh {program}\n"
        "rc=$?\n"
        'boundary_pre_end_sha256="$(sha256sum -- "$boundary_tool" | '
        'awk \'{print $1}\')"\n'
        'if [[ -L "$boundary_tool" || '
        '"$(readlink -f -- "$boundary_tool")" != '
        '"/mnt/tools-build/cosim_evidence_boundary" || '
        '"$boundary_pre_end_sha256" != "$boundary_sha256" ]]; then\n'
        '    rc=125\n'
        'else\n'
        'timeout --signal=TERM "${boundary_handshake_timeout_secs}s" '
        '"$boundary_tool" '
        'end "$boundary_token"\n'
        "boundary_rc=$?\n"
        'if [[ "$rc" -eq 0 && "$boundary_rc" -ne 0 ]]; then\n'
        '    rc="$boundary_rc"\n'
        "fi\n"
        "fi\n"
        f'echo "__{test_token}__:${{rc}}"\n'
        'exit "${rc}"\n'
    )


def parse_rfc3339nano(value: str) -> Optional[int]:
    """把严格 RFC3339Nano 时间戳转换为 UTC 纳秒。"""

    match = _RFC3339NANO_RE.fullmatch(value)
    if match is None:
        return None
    zone = match.group("zone")
    try:
        if zone == "Z":
            tz = timezone.utc
        else:
            offset_hours = int(zone[1:3])
            offset_minutes = int(zone[4:6])
            if offset_hours > 23 or offset_minutes > 59:
                return None
            direction = 1 if zone[0] == "+" else -1
            tz = timezone(
                direction * timedelta(hours=offset_hours, minutes=offset_minutes)
            )
        local = datetime(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
            int(match.group("hour")),
            int(match.group("minute")),
            int(match.group("second")),
            tzinfo=tz,
        )
    except (ValueError, OverflowError):
        return None
    utc = local.astimezone(timezone.utc)
    fraction = (match.group("fraction") or "").ljust(9, "0")
    fraction_ns = int(fraction) if fraction else 0
    return calendar.timegm(utc.timetuple()) * NANOSECONDS_PER_SECOND + fraction_ns


def split_docker_timestamp(line: str) -> Tuple[Optional[int], str]:
    """只剥离一个合法的 Docker RFC3339Nano 行前缀。"""

    match = _DOCKER_TIMESTAMP_RE.fullmatch(line)
    if match is None:
        return None, line
    timestamp_ns = parse_rfc3339nano(match.group("timestamp"))
    if timestamp_ns is None:
        return None, line
    return timestamp_ns, match.group("payload")


def simulator_fatal_kind(payload: str) -> Optional[str]:
    """返回模拟器致命行类型；普通警告和引用文本不命中。"""

    if _GEM5_FATAL_RE.match(payload):
        return "gem5_fatal"
    if _ASSERTION_RE.search(payload):
        return "assertion_failed"
    if _GEM5_ABORT_RE.fullmatch(payload):
        return "gem5_program_aborted"
    if _GEM5_SEGFAULT_RE.fullmatch(payload):
        return "gem5_segmentation_fault"
    shell_crash = _SHELL_CRASH_RE.fullmatch(payload)
    if shell_crash is None:
        shell_crash = _BASH_PROCESS_CRASH_RE.fullmatch(payload)
    if shell_crash is not None:
        return (
            "native_segmentation_fault"
            if shell_crash.group("kind") == "Segmentation fault"
            else "native_aborted"
        )
    if qemu_termination_signal(payload) is not None:
        return "qemu_terminated_on_signal"
    return None


def qemu_termination_signal(payload: str) -> Optional[int]:
    """返回 QEMU termination signal 编号。"""

    match = _QEMU_SIGNAL_RE.search(payload)
    return int(match.group("signal")) if match is not None else None


def _bounded_event(
    events: List[Dict[str, object]], line_number: int, kind: str, payload: str
) -> None:
    if len(events) < 20:
        events.append(
            {
                "kind": kind,
                "line": line_number,
                "text": payload[:512],
            }
        )


def analyze_qemu_log(
    path: Path,
    *,
    expected_run_id: str,
    expected_program: str,
    expected_hsa: str,
    expected_test_timeout: str,
    expected_boundary_helper_sha256: str = "",
) -> Dict[str, object]:
    """从单一稳定快照验证 QEMU/Guest 完成状态机。"""

    expected_run_sha = completion_token_run_sha256(expected_run_id)
    expected_compile_marker = (
        f"__COSIM_COMPILE_DONE_{expected_program}_{expected_run_sha}__:0"
    )
    expected_boundary_ready_marker = (
        f"__COSIM_BOUNDARY_READY_{expected_program}_{expected_run_sha}__:"
        f"{expected_boundary_helper_sha256}"
    )
    expected_test_marker = (
        f"__COSIM_TEST_DONE_{expected_program}_{expected_run_sha}__:0"
    )
    read_error: Optional[str] = None
    snapshot: Optional[StableLogSnapshot] = None

    run_events: List[Dict[str, object]] = []
    hsa_events: List[Dict[str, object]] = []
    timeout_policy_events: List[Dict[str, object]] = []
    compile_events: List[Dict[str, object]] = []
    boundary_ready_events: List[Dict[str, object]] = []
    pass_events: List[Dict[str, object]] = []
    test_events: List[Dict[str, object]] = []
    signal_events: List[Dict[str, object]] = []
    fatal_events: List[Dict[str, object]] = []
    expected_cleanup_events: List[Dict[str, object]] = []
    simulator_exit_lines: List[int] = []
    timeout_signal_lines: List[int] = []
    invalid_encoding_lines: List[int] = []
    suspicious_completion_lines: List[int] = []
    fail_count = 0
    line_count = 0

    def consume_line(line_number: int, raw_line: bytes) -> None:
        nonlocal fail_count, line_count

        line_count = line_number
        try:
            line = raw_line.rstrip(b"\r\n").decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            if len(invalid_encoding_lines) < 20:
                invalid_encoding_lines.append(line_number)
            return

        # Readline 会在执行粘贴到交互式 Guest shell 的命令前输出
        # bracketed-paste 结束序列和回车。终端随后从列 0 显示脚本的首行，
        # 因此只规范化这一种精确前缀，不接受任意 ANSI/回车改写。
        evidence_line = line.removeprefix(
            _READLINE_BRACKETED_PASTE_END_PREFIX
        )

        run_match = _RUN_MARKER_RE.fullmatch(evidence_line)
        if run_match is not None:
            run_events.append(
                {"line": line_number, "run_id": run_match.group("run_id")}
            )
        hsa_match = _HSA_RE.fullmatch(evidence_line)
        if hsa_match is not None:
            hsa_events.append(
                {"line": line_number, "value": hsa_match.group("value")}
            )
        timeout_match = _TEST_TIMEOUT_POLICY_RE.fullmatch(evidence_line)
        if timeout_match is not None:
            timeout_policy_events.append(
                {"line": line_number, "value": timeout_match.group("value")}
            )
        elif _TIMEOUT_SIGNAL_RE.match(evidence_line):
            if len(timeout_signal_lines) < 20:
                timeout_signal_lines.append(line_number)

        token_match = _COMPLETION_TOKEN_RE.fullmatch(evidence_line)
        if token_match is not None:
            event = {
                "line": line_number,
                "marker": evidence_line,
                "program": token_match.group("program"),
                "run_sha256": token_match.group("run_sha256"),
                "status": int(token_match.group("status")),
            }
            if token_match.group("role") == "COSIM_COMPILE_DONE":
                compile_events.append(event)
            else:
                test_events.append(event)
        elif evidence_line.startswith(
            ("__COSIM_COMPILE_DONE_", "__COSIM_TEST_DONE_")
        ):
            if len(suspicious_completion_lines) < 20:
                suspicious_completion_lines.append(line_number)

        boundary_ready_match = _BOUNDARY_READY_RE.fullmatch(evidence_line)
        if boundary_ready_match is not None:
            boundary_ready_events.append(
                {
                    "line": line_number,
                    "marker": evidence_line,
                    "program": boundary_ready_match.group("program"),
                    "run_sha256": boundary_ready_match.group("run_sha256"),
                    "sha256": boundary_ready_match.group("sha256"),
                }
            )
        elif evidence_line.startswith("__COSIM_BOUNDARY_READY_"):
            if len(suspicious_completion_lines) < 20:
                suspicious_completion_lines.append(line_number)

        pass_match = _PASS_RE.fullmatch(evidence_line)
        if pass_match is not None:
            pass_events.append(
                {"line": line_number, "program": pass_match.group("program")}
            )
        if _FAIL_RE.match(evidence_line):
            fail_count += 1
        if _SIMULATOR_EXIT_SIGNAL_RE.match(evidence_line):
            if len(simulator_exit_lines) < 20:
                simulator_exit_lines.append(line_number)
            _bounded_event(
                fatal_events,
                line_number,
                "simulator_exit_marker",
                line,
            )

        fatal_kind = simulator_fatal_kind(evidence_line)
        if fatal_kind == "qemu_terminated_on_signal":
            signal_events.append(
                {
                    "kind": fatal_kind,
                    "line": line_number,
                    "signal": qemu_termination_signal(evidence_line),
                    "text": line[:512],
                }
            )
        elif fatal_kind is not None:
            _bounded_event(fatal_events, line_number, fatal_kind, line)

    try:
        snapshot = read_stable_log_snapshot(path, consume_line)
    except OSError as error:
        read_error = str(error)

    exact_run = [
        event for event in run_events if event.get("run_id") == expected_run_id
    ]
    exact_hsa = [
        event for event in hsa_events if event.get("value") == expected_hsa
    ]
    exact_timeout = [
        event
        for event in timeout_policy_events
        if event.get("value") == expected_test_timeout
    ]
    exact_compile = [
        event
        for event in compile_events
        if event.get("marker") == expected_compile_marker
    ]
    exact_boundary_ready = [
        event
        for event in boundary_ready_events
        if event.get("marker") == expected_boundary_ready_marker
    ]
    exact_pass = [
        event
        for event in pass_events
        if event.get("program") == expected_program
    ]
    exact_test = [
        event for event in test_events if event.get("marker") == expected_test_marker
    ]
    required_event_pairs = [
        (run_events, exact_run),
        (hsa_events, exact_hsa),
        (timeout_policy_events, exact_timeout),
        (compile_events, exact_compile),
        (pass_events, exact_pass),
        (test_events, exact_test),
    ]
    if expected_boundary_helper_sha256:
        required_event_pairs.append(
            (boundary_ready_events, exact_boundary_ready)
        )
    elif boundary_ready_events:
        required_event_pairs.append((boundary_ready_events, []))
    unique_events_ok = all(
        len(events) == 1 and len(exact) == 1
        for events, exact in required_event_pairs
    ) and not suspicious_completion_lines

    selected = {
        "run": exact_run[0] if len(exact_run) == 1 else None,
        "hsa": exact_hsa[0] if len(exact_hsa) == 1 else None,
        "timeout": exact_timeout[0] if len(exact_timeout) == 1 else None,
        "compile": exact_compile[0] if len(exact_compile) == 1 else None,
        "boundary_ready": (
            exact_boundary_ready[0] if len(exact_boundary_ready) == 1 else None
        ),
        "pass": exact_pass[0] if len(exact_pass) == 1 else None,
        "test": exact_test[0] if len(exact_test) == 1 else None,
    }
    ordered_names = ["run", "hsa", "timeout", "compile"]
    if expected_boundary_helper_sha256:
        ordered_names.append("boundary_ready")
    ordered_names.extend(("pass", "test"))
    order_errors: List[str] = []
    if all(selected[name] is not None for name in ordered_names):
        selected_lines = [int(selected[name]["line"]) for name in ordered_names]
        if any(
            left >= right
            for left, right in zip(selected_lines, selected_lines[1:])
        ):
            order_errors.append("<".join(ordered_names))
    else:
        order_errors.append("missing_required_event")

    base_sequence_ok = unique_events_ok and not order_errors
    cleanup_signal_ok = not signal_events
    cleanup_signal_event: Optional[Dict[str, object]] = None
    if len(signal_events) == 1 and base_sequence_ok:
        candidate = signal_events[0]
        selected_test = selected["test"]
        if candidate.get("signal") == 15 and selected_test is not None and \
                int(candidate["line"]) > int(selected_test["line"]):
            cleanup_signal_ok = True
            cleanup_signal_event = candidate
            expected_cleanup_events.append(candidate)
    if not cleanup_signal_ok:
        for event in signal_events:
            if len(fatal_events) >= 20:
                break
            fatal_events.append(event)
        order_errors.append("invalid_cleanup_signal")

    sequence_ok = (
        base_sequence_ok
        and cleanup_signal_ok
        and fail_count == 0
        and not timeout_signal_lines
        and not simulator_exit_lines
    )
    snapshot_ok = snapshot is not None and read_error is None
    ok = (
        snapshot_ok
        and not invalid_encoding_lines
        and sequence_ok
        and not fatal_events
    )
    return {
        "expected_compile_marker": expected_compile_marker,
        "expected_boundary_ready_marker": expected_boundary_ready_marker,
        "expected_run_id": expected_run_id,
        "expected_run_sha256": expected_run_sha,
        "expected_test_marker": expected_test_marker,
        "expected_cleanup_events": expected_cleanup_events,
        "fail_count": fail_count,
        "fatal_count": len(fatal_events),
        "fatal_events": fatal_events,
        "hsa_values": [str(event["value"]) for event in hsa_events],
        "boundary_ready_marker_count": len(boundary_ready_events),
        "boundary_ready_markers": boundary_ready_events,
        "invalid_encoding_lines": invalid_encoding_lines,
        "line_count": line_count,
        "ok": ok,
        "order_errors": order_errors,
        "pass_count": len(exact_pass),
        "qemu_log_sha256": snapshot.sha256 if snapshot is not None else "",
        "read_error": read_error,
        "run_marker_count": len(run_events),
        "run_markers": run_events,
        "sequence": {
            **selected,
            "cleanup_signal": cleanup_signal_event,
            "ok": sequence_ok,
        },
        "simulator_exit_lines": simulator_exit_lines,
        "snapshot_stat_identity": (
            list(snapshot.stat_identity) if snapshot is not None else None
        ),
        "stable_snapshot_ok": snapshot_ok,
        "suspicious_completion_lines": suspicious_completion_lines,
        "test_timeout_values": [
            str(event["value"]) for event in timeout_policy_events
        ],
        "timeout_signal_lines": timeout_signal_lines,
    }


def analyze_gem5_log(
    path: Path,
    *,
    expected_run_id: str,
    expected_test_id: str = "",
    expected_boundary_token: str = "",
    test_started_at: str,
    test_finished_at: str,
) -> Dict[str, object]:
    """验证 gem5 诊断日志；GPU 执行事实由独立证据文件证明。"""

    started_ns = parse_rfc3339nano(test_started_at)
    finished_ns = parse_rfc3339nano(test_finished_at)
    window_ok = (
        started_ns is not None
        and finished_ns is not None
        and started_ns <= finished_ns
    )
    snapshot: Optional[StableLogSnapshot] = None
    read_error: Optional[str] = None
    line_count = 0
    content_line_count = 0
    timestamped_line_count = 0
    invalid_timestamp_lines: List[int] = []
    invalid_encoding_lines: List[int] = []
    timestamp_regression_lines: List[int] = []
    fatal_events: List[Dict[str, object]] = []
    fatal_count = 0
    command_lines: List[str] = []
    command_events: List[Dict[str, object]] = []
    previous_timestamp_ns: Optional[int] = None

    def consume_line(line_number: int, raw_line: bytes) -> None:
        nonlocal content_line_count, fatal_count, line_count
        nonlocal previous_timestamp_ns, timestamped_line_count

        line_count = line_number
        raw_content = raw_line.rstrip(b"\r\n")
        if not raw_content:
            return
        content_line_count += 1
        try:
            line = raw_content.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            if len(invalid_encoding_lines) < 20:
                invalid_encoding_lines.append(line_number)
            return
        timestamp_ns, payload = split_docker_timestamp(line)
        if timestamp_ns is None:
            if len(invalid_timestamp_lines) < 20:
                invalid_timestamp_lines.append(line_number)
            fatal_kind = simulator_fatal_kind(line)
            if fatal_kind is not None:
                fatal_count += 1
                _bounded_event(fatal_events, line_number, fatal_kind, line)
            return
        timestamped_line_count += 1
        if previous_timestamp_ns is not None and \
                timestamp_ns < previous_timestamp_ns:
            if len(timestamp_regression_lines) < 20:
                timestamp_regression_lines.append(line_number)
        previous_timestamp_ns = timestamp_ns
        fatal_kind = simulator_fatal_kind(payload)
        if fatal_kind is not None:
            fatal_count += 1
            _bounded_event(fatal_events, line_number, fatal_kind, payload)

        command_match = _COMMAND_RE.fullmatch(payload)
        if command_match is not None:
            command_lines.append(command_match.group("command"))
            command_events.append(
                {
                    "line": line_number,
                    "timestamp_ns": timestamp_ns,
                }
            )

    try:
        snapshot = read_stable_log_snapshot(path, consume_line)
    except OSError as error:
        read_error = str(error)

    command_parse_error: Optional[str] = None
    command_words: List[str] = []
    if len(command_lines) == 1:
        try:
            command_words = shlex.split(command_lines[0], posix=True)
        except ValueError as error:
            command_parse_error = str(error)

    expected_tokens = [
        f"--socket-path=/tmp/gem5-mi300x-{expected_run_id}.sock",
        f"--shmem-path=/mi300x-vram-{expected_run_id}",
        f"--shmem-host-path=/cosim-guest-ram-{expected_run_id}",
        f"--evidence-run-id={expected_run_id}",
        f"--evidence-path=/cosim-artifacts/{GPU_EVIDENCE_FILENAME}",
    ]
    boundary_identity_ok = bool(
        (not expected_test_id and not expected_boundary_token)
        or (
            _PROGRAM_RE.fullmatch(expected_test_id) is not None
            and re.fullmatch(r"[0-9a-f]{32}", expected_boundary_token)
            is not None
        )
    )
    if expected_test_id or expected_boundary_token:
        expected_tokens.extend(
            (
                f"--evidence-test-id={expected_test_id}",
                f"--evidence-token={expected_boundary_token}",
            )
        )
    missing_run_tokens = [
        token for token in expected_tokens if token not in command_words
    ]
    noncanonical_run_tokens = []
    for token in expected_tokens:
        prefix = f"{token.split('=', 1)[0]}="
        matching = [word for word in command_words if word.startswith(prefix)]
        if matching != [token]:
            noncanonical_run_tokens.append(
                {"expected": token, "observed": matching}
            )
    debug_values = [
        word.split("=", 1)[1]
        for word in command_words
        if word.startswith("--debug-flags=")
    ]
    debug_flags = debug_values[0].split(",") if len(debug_values) == 1 else []
    missing_debug_flags = [
        flag for flag in REQUIRED_STRICT_DEBUG_FLAGS if flag not in debug_flags
    ]
    command_identity_ok = (
        len(command_lines) == 1
        and command_parse_error is None
        and boundary_identity_ok
        and not missing_run_tokens
        and not noncanonical_run_tokens
        and len(debug_values) == 1
        and not missing_debug_flags
    )
    timestamp_contract_ok = (
        read_error is None
        and line_count > 0
        and not invalid_timestamp_lines
        and not invalid_encoding_lines
        and not timestamp_regression_lines
        and timestamped_line_count == content_line_count
    )
    command_event = command_events[0] if len(command_events) == 1 else None
    command_before_test_ok = bool(
        command_event is not None
        and started_ns is not None
        and int(command_event["timestamp_ns"]) <= started_ns
    )
    ok = (
        timestamp_contract_ok
        and fatal_count == 0
        and command_identity_ok
        and command_before_test_ok
        and window_ok
    )
    return {
        "command_before_test_ok": command_before_test_ok,
        "command_event": command_event,
        "command_identity_ok": command_identity_ok,
        "command_line_count": len(command_lines),
        "command_parse_error": command_parse_error,
        "command_words": command_words,
        "boundary_identity_ok": boundary_identity_ok,
        "content_line_count": content_line_count,
        "fatal_count": fatal_count,
        "fatal_events": fatal_events,
        "gem5_log_sha256": snapshot.sha256 if snapshot is not None else "",
        "invalid_encoding_lines": invalid_encoding_lines,
        "invalid_timestamp_lines": invalid_timestamp_lines,
        "line_count": line_count,
        "missing_debug_flags": missing_debug_flags,
        "missing_run_tokens": missing_run_tokens,
        "noncanonical_run_tokens": noncanonical_run_tokens,
        "ok": ok,
        "read_error": read_error,
        "snapshot_stat_identity": (
            list(snapshot.stat_identity) if snapshot is not None else None
        ),
        "stable_snapshot_ok": snapshot is not None and read_error is None,
        "test_finished_at": test_finished_at,
        "test_finished_ns": finished_ns,
        "test_started_at": test_started_at,
        "test_started_ns": started_ns,
        "timestamp_contract_ok": timestamp_contract_ok,
        "timestamp_regression_lines": timestamp_regression_lines,
        "timestamped_line_count": timestamped_line_count,
        "window_ok": window_ok,
    }


def _parse_evidence_boundary(value: object) -> Optional[int]:
    raw = str(value)
    if _NONNEGATIVE_INTEGER_RE.fullmatch(raw) is None:
        return None
    parsed = int(raw)
    return parsed if parsed <= (1 << 64) - 1 else None


def analyze_gpu_evidence(
    path: Path,
    *,
    expected_run_id: str,
    expected_num_gpus: object = None,
    require_test_boundaries: bool = False,
    start_seq: object = None,
    end_seq: object = None,
) -> Dict[str, object]:
    """验证 gem5 独占写入的结构化 GPU 事件与测试窗口因果链。"""

    snapshot: Optional[StableLogSnapshot] = None
    read_error: Optional[str] = None
    structural_errors: List[Dict[str, object]] = []
    event_counts = {event: 0 for event in _GPU_EVIDENCE_FIELDS}
    window_event_counts = {event: 0 for event in _GPU_EVIDENCE_FIELDS}
    records: List[Dict[str, int | str]] = []
    previous_tick: Optional[int] = None
    parsed_num_gpus = _parse_evidence_boundary(expected_num_gpus)
    configuration_ok = bool(
        expected_num_gpus is None
        or (parsed_num_gpus is not None and parsed_num_gpus > 0)
    )

    def add_structural_error(line: int, reason: str) -> None:
        if len(structural_errors) < 20:
            structural_errors.append({"line": line, "reason": reason})

    def consume_line(line_number: int, raw_line: bytes) -> None:
        nonlocal previous_tick
        if not raw_line.endswith(b"\n") or raw_line.endswith(b"\r\n"):
            add_structural_error(line_number, "record_framing")
            return
        raw_content = raw_line[:-1]
        try:
            line = raw_content.decode("ascii", errors="strict")
        except UnicodeDecodeError:
            add_structural_error(line_number, "record_encoding")
            return
        if line_number == 1:
            if line != GPU_EVIDENCE_HEADER:
                add_structural_error(line_number, "header")
            return
        fields = line.split("\t")
        if len(fields) != 9:
            add_structural_error(line_number, "field_count")
            return
        magic, run_id, raw_seq, raw_tick, event, *raw_values = fields
        if magic != GPU_EVIDENCE_MAGIC:
            add_structural_error(line_number, "schema")
            return
        if run_id != expected_run_id or _RUN_ID_RE.fullmatch(run_id) is None or \
                ".." in run_id:
            add_structural_error(line_number, "run_id")
            return
        if _NONNEGATIVE_INTEGER_RE.fullmatch(raw_seq) is None or \
                _NONNEGATIVE_INTEGER_RE.fullmatch(raw_tick) is None:
            add_structural_error(line_number, "sequence_or_tick")
            return
        if any(_EVIDENCE_INTEGER_RE.fullmatch(value) is None for value in raw_values):
            add_structural_error(line_number, "event_integer")
            return
        seq = int(raw_seq)
        tick = int(raw_tick)
        values = [int(value) for value in raw_values]
        if seq > (1 << 64) - 1 or tick > (1 << 64) - 1 or \
                any(value > (1 << 63) - 1 for value in values):
            add_structural_error(line_number, "integer_range")
            return
        if seq != line_number - 2:
            add_structural_error(line_number, "noncontiguous_sequence")
        if previous_tick is not None and tick < previous_tick:
            add_structural_error(line_number, "tick_regression")
        previous_tick = tick
        expected_fields = _GPU_EVIDENCE_FIELDS.get(event)
        if expected_fields is None:
            add_structural_error(line_number, "event_type")
            return
        for required, value in zip(expected_fields, values):
            if (required and value < 0) or (not required and value != -1):
                add_structural_error(line_number, "event_shape")
                return
        if expected_fields[0] and parsed_num_gpus is not None and \
                values[0] >= parsed_num_gpus:
            add_structural_error(line_number, "gpu_range")
            return
        event_counts[event] += 1
        records.append(
            {
                "cu": values[3],
                "dispatch": values[1],
                "event": event,
                "gpu": values[0],
                "line": line_number,
                "seq": seq,
                "tick": tick,
                "wg": values[2],
            }
        )

    try:
        snapshot = read_stable_log_snapshot(path, consume_line)
    except OSError as error:
        read_error = str(error)

    mode_ok = bool(
        snapshot is not None
        and stat.S_IMODE(snapshot.stat_identity[2]) == 0o600
    )
    structural_ok = bool(
        snapshot is not None
        and read_error is None
        and configuration_ok
        and mode_ok
        and snapshot.line_count > 1
        and not structural_errors
        and len(records) == snapshot.line_count - 1
        and event_counts["session_start"] == 1
        and records
        and records[0]["event"] == "session_start"
        and records[0]["seq"] == 0
    )
    begin_records = [
        record for record in records if record["event"] == "test_begin"
    ]
    end_records = [
        record for record in records if record["event"] == "test_end"
    ]
    explicit_start = _parse_evidence_boundary(start_seq)
    explicit_end = _parse_evidence_boundary(end_seq)
    boundary_required = bool(
        require_test_boundaries
        or start_seq is not None
        or end_seq is not None
    )
    inferred_start = int(begin_records[0]["seq"]) \
        if len(begin_records) == 1 else None
    inferred_end = int(end_records[0]["seq"]) \
        if len(end_records) == 1 else None
    parsed_start = explicit_start if start_seq is not None else inferred_start
    parsed_end = explicit_end if end_seq is not None else inferred_end
    boundary_ok = bool(
        not boundary_required
        or (
            len(begin_records) == 1
            and len(end_records) == 1
            and parsed_start is not None
            and parsed_end is not None
            and parsed_start < parsed_end
            and records
            and inferred_start == parsed_start
            and inferred_end == parsed_end
            and begin_records[0]["gpu"] == end_records[0]["gpu"]
            and parsed_end == int(records[-1]["seq"])
        )
    )

    clients: Dict[int, Dict[str, int | str]] = {}
    launches: Dict[Tuple[int, int], Dict[str, int | str]] = {}
    dispatches: Dict[Tuple[int, int, int], Dict[str, int | str]] = {}
    completions: Dict[Tuple[int, int, int], Dict[str, int | str]] = {}
    kernel_completions: Dict[Tuple[int, int], Dict[str, int | str]] = {}
    causal_errors: List[str] = []
    selected: Optional[Dict[str, Dict[str, int | str]]] = None
    if structural_ok and boundary_ok and parsed_start is not None and \
            parsed_end is not None:
        for record in records:
            event = str(record["event"])
            seq = int(record["seq"])
            gpu = int(record["gpu"])
            dispatch = int(record["dispatch"])
            wg = int(record["wg"])
            if event == "client_connected":
                if seq > parsed_start:
                    causal_errors.append(f"client_after_start:{seq}")
                elif gpu in clients:
                    causal_errors.append(f"duplicate_client:{seq}")
                else:
                    clients[gpu] = record
            if not parsed_start < seq < parsed_end:
                continue
            window_event_counts[event] += 1
            kernel_key = (gpu, dispatch)
            workgroup_key = (gpu, dispatch, wg)
            if event == "kernel_launch":
                if gpu not in clients:
                    causal_errors.append(f"launch_without_client:{seq}")
                elif kernel_key in launches:
                    causal_errors.append(f"duplicate_launch:{seq}")
                else:
                    launches[kernel_key] = record
            elif event == "workgroup_dispatch":
                if kernel_key not in launches:
                    causal_errors.append(f"dispatch_without_launch:{seq}")
                elif kernel_key in kernel_completions:
                    causal_errors.append(
                        f"dispatch_after_kernel_completion:{seq}"
                    )
                elif workgroup_key in dispatches:
                    causal_errors.append(f"duplicate_workgroup_dispatch:{seq}")
                else:
                    dispatches[workgroup_key] = record
            elif event == "workgroup_complete":
                if workgroup_key not in dispatches:
                    causal_errors.append(f"completion_without_dispatch:{seq}")
                elif kernel_key in kernel_completions:
                    causal_errors.append(
                        f"workgroup_after_kernel_completion:{seq}"
                    )
                elif workgroup_key in completions:
                    causal_errors.append(f"duplicate_workgroup_completion:{seq}")
                else:
                    completions[workgroup_key] = record
            elif event == "kernel_complete":
                if kernel_key not in launches:
                    causal_errors.append(f"kernel_completion_without_launch:{seq}")
                elif kernel_key in kernel_completions:
                    causal_errors.append(f"duplicate_kernel_completion:{seq}")
                elif not any(key[:2] == kernel_key for key in completions):
                    causal_errors.append(
                        f"kernel_completion_without_workgroup:{seq}"
                    )
                else:
                    kernel_completions[kernel_key] = record

        if set(dispatches) != set(completions):
            causal_errors.append("workgroup_set_mismatch")
        if set(launches) != set(kernel_completions):
            causal_errors.append("kernel_set_mismatch")
        if parsed_num_gpus is not None and \
                set(clients) != set(range(parsed_num_gpus)):
            causal_errors.append("client_set_mismatch")
        if launches and not causal_errors:
            kernel_key = min(
                launches,
                key=lambda key: int(launches[key]["seq"]),
            )
            workgroup_key = min(
                (key for key in dispatches if key[:2] == kernel_key),
                key=lambda key: int(dispatches[key]["seq"]),
            )
            selected = {
                "client": clients[kernel_key[0]],
                "completion": completions[workgroup_key],
                "dispatch": dispatches[workgroup_key],
                "kernel_completion": kernel_completions[kernel_key],
                "launch": launches[kernel_key],
            }

    sequence_ok = selected is not None and not causal_errors
    ok = structural_ok and boundary_ok and (
        not boundary_required or sequence_ok
    )
    return {
        "boundary_ok": boundary_ok,
        "boundary_events": {
            "begin": begin_records[0] if len(begin_records) == 1 else None,
            "end": end_records[0] if len(end_records) == 1 else None,
        },
        "client_connected_count": event_counts["client_connected"],
        "configuration_ok": configuration_ok,
        "causal_errors": causal_errors[:20],
        "end_seq": parsed_end,
        "event_counts": event_counts,
        "expected_num_gpus": parsed_num_gpus,
        "final_seq": int(records[-1]["seq"]) if records else None,
        "gpu_evidence_sha256": snapshot.sha256 if snapshot is not None else "",
        "gpu_sequence": {**(selected or {}), "ok": sequence_ok},
        "line_count": snapshot.line_count if snapshot is not None else 0,
        "mode_ok": mode_ok,
        "ok": ok,
        "read_error": read_error,
        "snapshot_stat_identity": (
            list(snapshot.stat_identity) if snapshot is not None else None
        ),
        "stable_snapshot_ok": snapshot is not None and read_error is None,
        "start_seq": parsed_start,
        "structural_errors": structural_errors,
        "structural_ok": structural_ok,
        "window_event_counts": window_event_counts,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    """提供给 shell producer 的稳定日志与 Guest 脚本入口。"""

    parser = argparse.ArgumentParser(description="验证并哈希稳定的 cosim 日志")
    subparsers = parser.add_subparsers(dest="command", required=True)
    stable_parser = subparsers.add_parser(
        "stable-sha256", help="拒绝符号链接并对稳定普通文件计算 SHA-256"
    )
    stable_parser.add_argument("path", type=Path)
    render_parser = subparsers.add_parser(
        "render-guest-script", help="输出 canonical Guest 执行脚本"
    )
    render_parser.add_argument("--program", required=True)
    render_parser.add_argument("--run-id", required=True)
    render_parser.add_argument("--hsa-enable-interrupt", required=True)
    render_parser.add_argument("--test-timeout", required=True)
    cursor_parser = subparsers.add_parser(
        "evidence-cursor", help="输出稳定结构化 GPU 证据的最后序号"
    )
    cursor_parser.add_argument("path", type=Path)
    cursor_parser.add_argument("--run-id", required=True)
    boundaries_parser = subparsers.add_parser(
        "evidence-boundaries", help="输出流内 test_begin/test_end 序号"
    )
    boundaries_parser.add_argument("path", type=Path)
    boundaries_parser.add_argument("--run-id", required=True)
    token_parser = subparsers.add_parser(
        "boundary-token", help="输出绑定 run/program 的 AQL boundary token"
    )
    token_parser.add_argument("--program", required=True)
    token_parser.add_argument("--run-id", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "stable-sha256":
            print(stable_log_sha256(args.path))
            return 0
        if args.command == "render-guest-script":
            sys.stdout.write(
                render_guest_run_script(
                    program=args.program,
                    run_id=args.run_id,
                    hsa_enable_interrupt=args.hsa_enable_interrupt,
                    test_timeout=args.test_timeout,
                )
            )
            return 0
        if args.command == "boundary-token":
            print(evidence_boundary_token(args.run_id, args.program))
            return 0
        if args.command == "evidence-cursor":
            analysis = analyze_gpu_evidence(
                args.path,
                expected_run_id=args.run_id,
            )
            if analysis.get("ok") is not True or \
                    analysis.get("final_seq") is None:
                raise ValueError(
                    "结构化 GPU 证据无效："
                    f"read_error={analysis.get('read_error')!r}, "
                    f"errors={analysis.get('structural_errors')!r}"
                )
            print(analysis["final_seq"])
            return 0
        if args.command == "evidence-boundaries":
            analysis = analyze_gpu_evidence(
                args.path,
                expected_run_id=args.run_id,
                require_test_boundaries=True,
            )
            if analysis.get("structural_ok") is not True or \
                    analysis.get("boundary_ok") is not True:
                raise ValueError(
                    "结构化 GPU boundary 无效："
                    f"read_error={analysis.get('read_error')!r}, "
                    f"events={analysis.get('boundary_events')!r}, "
                    f"errors={analysis.get('structural_errors')!r}"
                )
            print(f"{analysis['start_seq']}\t{analysis['end_seq']}")
            return 0
    except OSError as error:
        print(f"日志稳定哈希失败：{error}", file=sys.stderr)
        return 1
    except ValueError as error:
        print(f"证据处理失败：{error}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
