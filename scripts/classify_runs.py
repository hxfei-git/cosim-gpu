#!/usr/bin/env python3
"""Classify archived cosim runs from their durable evidence.

This module deliberately treats PASS as an evidence contract, not as a log
keyword.  A run passes only when its exact program identity, build result,
guest result, simulator lifetime, cleanup, and provenance can all be proven
from one artifact directory.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, List, Mapping, Optional, Sequence, Tuple


SCHEMA = "cosim-run-verdict/v1"

TIMEOUT_CATEGORIES = {
    "boot_timeout",
    "gem5_init_timeout",
    "test_timeout",
    "timeout",
}
SIMULATOR_EXIT_CATEGORIES = {
    "gem5_exit",
    "qemu_exit",
    "launcher_exit",
    "simulator_early_exit",
}
SUCCESS_CATEGORIES = {"test_pass", "pass"}
CLEANUP_SUCCESS = {"ok", "pass", "passed", "success", "verified"}
CLEANUP_FAILURE = {"fail", "failed", "failure", "incomplete", "unsafe"}

METADATA_NAMES = ("metadata.txt", "runner-metadata.txt")
GUEST_LOG_NAMES = (
    "logs/guest/test.log",
    "logs/guest.log",
    "guest.log",
    "qemu.log",
    "qemu-console.log",
    "logs/qemu.log",
    "logs/qemu-console.log",
)
QEMU_LOG_NAMES = (
    "qemu.log",
    "qemu-console.log",
    "logs/qemu.log",
    "logs/qemu-console.log",
)
GEM5_LOG_NAMES = ("gem5.log", "logs/gem5.log")
SOURCE_SNAPSHOT_NAMES = ("patch/source-snapshot.txt", "source-snapshot.txt")
PROVENANCE_NAMES = (
    "patch/binary-provenance.txt",
    "binary-provenance.txt",
)
GEM5_STATUS_NAMES = ("patch/gem5-status.txt", "gem5-status.txt")
GEM5_PATCH_NAMES = ("patch/gem5.patch", "gem5.patch")
UNTRACKED_LIST_NAMES = (
    "patch/untracked-files.txt",
    "untracked-files.txt",
)
UNTRACKED_ARCHIVE_NAMES = (
    "patch/untracked-files.tar",
    "untracked-files.tar",
)

ENV_RE = re.compile(r"^\[COSIM_ENV\] HSA_ENABLE_INTERRUPT=([01])$")
FAIL_RE = re.compile(r"^\[FAIL\](?:\s|$)")
TIMEOUT_SIGNAL_RE = re.compile(
    r"^\[(?:COSIM_)?(?:BOOT_|GEM5_INIT_|TEST_)?TIMEOUT\](?:\s|$)"
)
TIMEOUT_POLICY_RE = re.compile(
    r"^\[COSIM_TIMEOUT\] TEST_TIMEOUT_SECS=[1-9][0-9]*$"
)
SIMULATOR_EXIT_SIGNAL_RE = re.compile(
    r"^\[COSIM_(?:GEM5|QEMU|SIMULATOR|LAUNCHER)_EXIT\](?:\s|$)"
)
SIMULATOR_FATAL_RE = re.compile(
    r"^(?:(?:gem5\s+)?(?:panic|fatal):|Assertion .+ failed\.?$|"
    r"qemu-system-[^:]+: terminating on signal)"
)


@dataclass(frozen=True)
class LocatedFile:
    role: str
    path: Optional[Path]
    required_nonempty: bool = True

    @property
    def present(self) -> bool:
        if self.path is None or not self.path.is_file():
            return False
        return not self.required_nonempty or self.path.stat().st_size > 0


def _first_file(root: Path, names: Sequence[str]) -> Optional[Path]:
    for name in names:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def locate_evidence(root: Path) -> Dict[str, LocatedFile]:
    """Return the canonical raw evidence roles for one run."""

    return {
        "metadata": LocatedFile("metadata", _first_file(root, METADATA_NAMES)),
        "guest_log": LocatedFile("guest_log", _first_file(root, GUEST_LOG_NAMES)),
        "qemu_log": LocatedFile("qemu_log", _first_file(root, QEMU_LOG_NAMES)),
        "gem5_log": LocatedFile("gem5_log", _first_file(root, GEM5_LOG_NAMES)),
        "source_snapshot": LocatedFile(
            "source_snapshot", _first_file(root, SOURCE_SNAPSHOT_NAMES)
        ),
        "binary_provenance": LocatedFile(
            "binary_provenance", _first_file(root, PROVENANCE_NAMES)
        ),
        "gem5_status": LocatedFile(
            # git status --short is empty for the desired clean-tree case.
            "gem5_status", _first_file(root, GEM5_STATUS_NAMES), False
        ),
        # A clean source tree has a deliberately empty patch.
        "gem5_patch": LocatedFile(
            "gem5_patch", _first_file(root, GEM5_PATCH_NAMES), False
        ),
        "untracked_files": LocatedFile(
            "untracked_files", _first_file(root, UNTRACKED_LIST_NAMES), False
        ),
        "untracked_archive": LocatedFile(
            "untracked_archive", _first_file(root, UNTRACKED_ARCHIVE_NAMES)
        ),
    }


def read_key_values(path: Optional[Path]) -> Tuple[Dict[str, str], Dict[str, int]]:
    values: Dict[str, str] = {}
    lines: Dict[str, int] = {}
    if path is None or not path.is_file():
        return values, lines

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_number, raw_line in enumerate(handle, 1):
            line = raw_line.rstrip("\r\n")
            if not line or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key:
                continue
            values[key] = value.strip()
            lines[key] = line_number
    return values, lines


def _first_value(values: Mapping[str, str], names: Sequence[str]) -> Optional[str]:
    for name in names:
        value = values.get(name)
        if value is not None and value != "":
            return value
    return None


def _parse_int(value: Optional[str]) -> Optional[int]:
    if value is None or not re.fullmatch(r"-?[0-9]+", value):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _relative_or_absolute(path: Optional[Path]) -> Optional[str]:
    return str(path) if path is not None else None


def _iter_normalized_lines(path: Optional[Path]) -> Iterator[Tuple[int, str]]:
    if path is None or not path.is_file():
        return
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_number, raw_line in enumerate(handle, 1):
            yield line_number, raw_line.rstrip("\n").rstrip("\r")


def _identity_check(
    metadata: Mapping[str, str], requested_program: Optional[str]
) -> Tuple[Optional[str], bool, str, Dict[str, Optional[str]]]:
    recorded_program = _first_value(metadata, ("program", "test", "expected_program"))
    expected_program = requested_program or recorded_program
    source = _first_value(
        metadata, ("program_source", "source_path", "source")
    )
    binary = _first_value(
        metadata, ("program_binary", "binary_path", "test_binary")
    )
    runner_argument = _first_value(
        metadata, ("runner_argument", "runner_arg")
    )
    details: Dict[str, Optional[str]] = {
        "requested_program": requested_program,
        "recorded_program": recorded_program,
        "program_source": source,
        "program_binary": binary,
        "runner_argument": runner_argument,
    }

    missing = [
        name
        for name, value in (
            ("program", recorded_program),
            ("program_source", source),
            ("program_binary", binary),
            ("runner_argument", runner_argument),
        )
        if not value
    ]
    if expected_program is None or missing:
        return expected_program, False, "program_identity_incomplete", details

    claimed_values = [
        metadata[name]
        for name in ("program", "test", "expected_program")
        if metadata.get(name)
    ]
    if any(value != expected_program for value in claimed_values):
        return expected_program, False, "program_identity_mismatch", details
    if runner_argument != expected_program:
        return expected_program, False, "program_identity_mismatch", details

    # Local kernels have an unambiguous source and binary basename contract.
    if "/" not in expected_program:
        expected_source = f"tests/kernels/{expected_program}.cpp"
        expected_binary = f"tests/build/{expected_program}"
        if source != expected_source or binary != expected_binary:
            return expected_program, False, "program_identity_mismatch", details

    return expected_program, True, "ok", details


def _source_snapshot_check(path: Optional[Path]) -> Tuple[bool, List[str]]:
    values, _ = read_key_values(path)
    problems: List[str] = []
    if not values.get("head_commit"):
        problems.append("source_snapshot:head_commit")
    if not values.get("source_fingerprint"):
        problems.append("source_snapshot:source_fingerprint")
    if any(value == "not_a_git_repository" for key, value in values.items() if "error" in key):
        problems.append("source_snapshot:not_a_git_repository")
    return not problems, problems


def _provenance_check(path: Optional[Path]) -> Tuple[bool, List[str], Dict[str, str]]:
    values, _ = read_key_values(path)
    missing = [
        f"binary_provenance:{key}"
        for key in ("gem5_source_commit", "gem5_binary", "gem5_sha256")
        if not values.get(key)
    ]
    if values.get("gem5_sha256") and not re.fullmatch(
        r"[0-9a-fA-F]{64}", values["gem5_sha256"]
    ):
        missing.append("binary_provenance:invalid_gem5_sha256")
    return not missing, missing, values


def _has_untracked_files(path: Optional[Path]) -> bool:
    if path is None or not path.is_file():
        return False
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return any(line.strip() for line in handle)


def classify_artifact(
    artifact_dir: Path, requested_program: Optional[str] = None
) -> Dict[str, object]:
    root = artifact_dir.resolve()
    evidence = locate_evidence(root)
    metadata, _ = read_key_values(evidence["metadata"].path)

    program, identity_ok, identity_reason, identity = _identity_check(
        metadata, requested_program
    )

    compile_raw = _first_value(metadata, ("compile_exit_code", "build_exit_code"))
    compile_exit_code = _parse_int(compile_raw)
    compile_ok = compile_exit_code == 0

    test_raw = _first_value(metadata, ("test_exit_code", "exit_code"))
    test_exit_code = _parse_int(test_raw)
    exit_ok = test_exit_code == 0

    cleanup_status = _first_value(metadata, ("cleanup_status", "cleanup"))
    cleanup_exit_raw = _first_value(metadata, ("cleanup_exit_code",))
    cleanup_exit_code = _parse_int(cleanup_exit_raw)
    cleanup_proven = cleanup_status is not None or cleanup_exit_raw is not None
    cleanup_ok = cleanup_proven
    if cleanup_status is not None:
        normalized_cleanup = cleanup_status.lower()
        cleanup_ok = cleanup_ok and normalized_cleanup in CLEANUP_SUCCESS
        if normalized_cleanup in CLEANUP_FAILURE:
            cleanup_ok = False
    if cleanup_exit_raw is not None:
        cleanup_ok = cleanup_ok and cleanup_exit_code == 0

    category = (metadata.get("category") or "").lower()
    timeout_seen = category in TIMEOUT_CATEGORIES
    simulator_exit_seen = category in SIMULATOR_EXIT_CATEGORIES
    if category == "cleanup_fail":
        cleanup_proven = True
        cleanup_ok = False

    pass_count = 0
    fail_count = 0
    env_values: List[str] = []
    guest_log = evidence["guest_log"].path
    if guest_log is not None:
        exact_pass = f"[PASS] {program}" if program else None
        for _, line in _iter_normalized_lines(guest_log):
            if exact_pass is not None and line == exact_pass:
                pass_count += 1
            if FAIL_RE.match(line):
                fail_count += 1
            env_match = ENV_RE.match(line)
            if env_match:
                env_values.append(env_match.group(1))
            if TIMEOUT_SIGNAL_RE.match(line) and not TIMEOUT_POLICY_RE.fullmatch(
                line
            ):
                timeout_seen = True
            if SIMULATOR_EXIT_SIGNAL_RE.match(line):
                simulator_exit_seen = True

    scanned_logs = {guest_log.resolve()} if guest_log is not None else set()
    for role in ("qemu_log", "gem5_log"):
        path = evidence[role].path
        if path is None or path.resolve() in scanned_logs:
            continue
        scanned_logs.add(path.resolve())
        for _, line in _iter_normalized_lines(path):
            if TIMEOUT_SIGNAL_RE.match(line) and not TIMEOUT_POLICY_RE.fullmatch(
                line
            ):
                timeout_seen = True
            if SIMULATOR_EXIT_SIGNAL_RE.match(line) or SIMULATOR_FATAL_RE.match(line):
                simulator_exit_seen = True

    snapshot_ok, snapshot_problems = _source_snapshot_check(
        evidence["source_snapshot"].path
    )
    provenance_ok, provenance_problems, provenance = _provenance_check(
        evidence["binary_provenance"].path
    )

    missing_evidence: List[str] = []
    for role in (
        "metadata",
        "guest_log",
        "qemu_log",
        "gem5_log",
        "source_snapshot",
        "binary_provenance",
        "gem5_status",
        "gem5_patch",
    ):
        if not evidence[role].present:
            missing_evidence.append(role)
    missing_evidence.extend(snapshot_problems)
    missing_evidence.extend(provenance_problems)
    status_path = evidence["gem5_status"].path
    patch_path = evidence["gem5_patch"].path
    if status_path is not None and status_path.is_file():
        tracked_changes = []
        for _, line in _iter_normalized_lines(status_path):
            if len(line) >= 2 and line[:2] != "??" and line[:2].strip():
                tracked_changes.append(line)
        if tracked_changes and (
            patch_path is None or not patch_path.is_file() or patch_path.stat().st_size == 0
        ):
            missing_evidence.append("source_snapshot:tracked_changes_without_patch")

    if compile_raw is None:
        missing_evidence.append("metadata:compile_exit_code")
    elif compile_exit_code is None:
        missing_evidence.append("metadata:invalid_compile_exit_code")
    if test_raw is None:
        missing_evidence.append("metadata:test_exit_code")
    elif test_exit_code is None:
        missing_evidence.append("metadata:invalid_test_exit_code")
    if not category:
        missing_evidence.append("metadata:category")
    if not cleanup_proven:
        missing_evidence.append("metadata:cleanup_status")
    if not env_values:
        missing_evidence.append("guest_log:hsa_enable_interrupt")
    elif len(set(env_values)) != 1:
        missing_evidence.append("guest_log:conflicting_hsa_enable_interrupt")
    if _has_untracked_files(evidence["untracked_files"].path):
        if not evidence["untracked_archive"].present:
            missing_evidence.append("untracked_archive")

    reasons: List[str] = []
    if not identity_ok:
        reasons.append(identity_reason)
    if compile_exit_code is not None and compile_exit_code != 0:
        reasons.append("compile_failure")
    if timeout_seen:
        reasons.append("timeout")
    if simulator_exit_seen:
        reasons.append("simulator_early_exit")
    if test_exit_code is not None and test_exit_code != 0:
        reasons.append("nonzero_test_exit")
    if fail_count:
        reasons.append("fail_marker_present")
    if pass_count != 1:
        reasons.append("invalid_pass_marker_count")
    if category and category not in SUCCESS_CATEGORIES:
        reasons.append("reported_nonpass_category")
    if cleanup_proven and not cleanup_ok:
        reasons.append("cleanup_failure")
    if missing_evidence:
        reasons.append("evidence_incomplete")

    # Preserve reason order while avoiding duplicate causes.
    reasons = list(dict.fromkeys(reasons))
    outcome = "PASS" if not reasons else "FAIL"
    primary_reason = "all_acceptance_gates_passed" if outcome == "PASS" else reasons[0]

    result: Dict[str, object] = {
        "schema": SCHEMA,
        "artifact_dir": str(root),
        "program": program,
        "outcome": outcome,
        "reason": primary_reason,
        "reasons": reasons,
        "exit_code": test_exit_code,
        "checks": {
            "program_identity": {
                "ok": identity_ok,
                "reason": identity_reason,
                **identity,
            },
            "compile": {
                "ok": compile_ok,
                "exit_code": compile_exit_code,
            },
            "test_exit": {"ok": exit_ok, "exit_code": test_exit_code},
            "markers": {
                "ok": pass_count == 1 and fail_count == 0,
                "exact_pass_count": pass_count,
                "fail_count": fail_count,
            },
            "timeout": {"ok": not timeout_seen, "observed": timeout_seen},
            "simulator_lifetime": {
                "ok": not simulator_exit_seen,
                "early_exit_observed": simulator_exit_seen,
            },
            "cleanup": {
                "ok": cleanup_ok,
                "status": cleanup_status,
                "exit_code": cleanup_exit_code,
            },
            "required_evidence": {
                "ok": not missing_evidence,
                "missing": sorted(set(missing_evidence)),
            },
            "source_snapshot": {"ok": snapshot_ok},
            "binary_provenance": {"ok": provenance_ok},
            "effective_environment": {
                "ok": len(set(env_values)) == 1 and bool(env_values),
                "hsa_enable_interrupt": env_values[-1] if env_values else None,
            },
        },
        "evidence": {
            role: _relative_or_absolute(item.path) for role, item in evidence.items()
        },
        "provenance": {
            key: provenance.get(key)
            for key in ("gem5_source_commit", "gem5_binary", "gem5_sha256")
        },
    }
    return result


def discover_artifact_dirs(root: Path) -> List[Path]:
    """Find run artifact directories without following unrelated trees."""

    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"artifact root is not a directory: {root}")
    found = {
        path.parent.resolve()
        for name in (*METADATA_NAMES, "verdict.json")
        for path in root.rglob(name)
        if path.is_file()
    }
    if any((root / name).is_file() for name in METADATA_NAMES) or (
        root / "verdict.json"
    ).is_file():
        found.add(root)
    return sorted(found, key=lambda path: str(path)) or [root]


def _artifact_dirs_from_matrix(matrix: Path) -> List[Path]:
    result: List[Path] = []
    with matrix.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if not reader.fieldnames or "artifact_dir" not in reader.fieldnames:
            raise ValueError(f"matrix lacks artifact_dir column: {matrix}")
        for row in reader:
            raw = (row.get("artifact_dir") or "").strip()
            if not raw:
                continue
            candidate = Path(raw)
            if not candidate.is_absolute():
                candidate = (matrix.parent / candidate).resolve()
            result.append(candidate)
    return result


def collect_artifact_dirs(
    artifact_dirs: Sequence[Path], log_dir: Optional[Path], matrix: Optional[Path]
) -> List[Path]:
    collected: List[Path] = [path.resolve() for path in artifact_dirs]
    if log_dir is not None:
        collected.extend(discover_artifact_dirs(log_dir))
    if matrix is not None:
        collected.extend(_artifact_dirs_from_matrix(matrix))

    unique: List[Path] = []
    seen = set()
    for path in collected:
        canonical = path.resolve()
        if canonical not in seen:
            unique.append(canonical)
            seen.add(canonical)
    return unique


def write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifact-dir",
        action="append",
        default=[],
        type=Path,
        help="one run artifact directory (repeatable)",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        help="legacy name for a root containing one or more run artifacts",
    )
    parser.add_argument("--matrix", type=Path, help="TSV with artifact_dir rows")
    parser.add_argument(
        "--program", help="expected exact program identity (single artifact only)"
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument(
        "--write-verdict",
        type=Path,
        help="atomically write the single classification to this JSON file",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.artifact_dir and args.log_dir is None and args.matrix is None:
        parser.error("one of --artifact-dir, --log-dir, or --matrix is required")

    try:
        artifact_dirs = collect_artifact_dirs(
            args.artifact_dir, args.log_dir, args.matrix
        )
    except (OSError, ValueError) as error:
        parser.error(str(error))
    if args.program and len(artifact_dirs) != 1:
        parser.error("--program requires exactly one artifact directory")
    if args.write_verdict and len(artifact_dirs) != 1:
        parser.error("--write-verdict requires exactly one artifact directory")

    results = [
        classify_artifact(path, args.program if len(artifact_dirs) == 1 else None)
        for path in artifact_dirs
    ]
    payload: object = results[0] if len(results) == 1 else results

    if args.write_verdict:
        write_json_atomic(args.write_verdict.resolve(), payload)
    if args.json:
        json.dump(payload, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        writer = csv.writer(sys.stdout, delimiter="\t", lineterminator="\n")
        writer.writerow(("program", "outcome", "reason", "artifact_dir"))
        for result in results:
            writer.writerow(
                (
                    result.get("program") or "",
                    result["outcome"],
                    result["reason"],
                    result["artifact_dir"],
                )
            )

    return 0 if all(result["outcome"] == "PASS" for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
