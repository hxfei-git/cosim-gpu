#!/usr/bin/env python3
"""Verify a top-level cosim matrix against its accepted run manifest rows.

The manifest records authorization and intent.  It is not execution evidence.
An accepted row is complete only when the top-level matrix joins it exactly
once to one self-consistent artifact directory with replayable provenance.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Dict, List, Mapping, MutableMapping, Optional, Sequence


SCHEMA = "cosim-matrix-verification/v2"
REPO_ROOT = Path(__file__).resolve().parents[1]
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40,64}$")
HSA_RE = re.compile(r"^\[COSIM_ENV\] HSA_ENABLE_INTERRUPT=([01])$")
TEST_TIMEOUT_RE = re.compile(r"^\[COSIM_TIMEOUT\] TEST_TIMEOUT_SECS=([1-9][0-9]*)$")
PREFIX_HSA_RE = re.compile(r"(?:^|\s)HSA_ENABLE_INTERRUPT=([01])(?:\s|$)")
PROGRAM_RE = re.compile(r"^[a-z0-9_]+$")
MEMORY_SIZE_RE = re.compile(r"^[1-9][0-9]*(?:[KMGTPE]i?B?)?$")
DEBUG_FLAGS_RE = re.compile(r"^[A-Za-z0-9_,]+$")
COMPILE_TOKEN_SCRIPT_RE = re.compile(
    r'^echo "__(COSIM_COMPILE_DONE_[A-Za-z0-9_]+)__:\$\{build_rc\}"$'
)
TEST_TOKEN_SCRIPT_RE = re.compile(
    r'^echo "__(COSIM_TEST_DONE_[A-Za-z0-9_]+)__:\$\{rc\}"$'
)

MANIFEST_COLUMNS = {
    "row_id",
    "program",
    "program_source",
    "source_sha256",
    "program_binary",
    "runner_argument",
    "strict_acceptance",
    "mode",
    "repeat_count",
    "timeout_policy",
    "boot_timeout",
    "test_timeout",
    "guest_run_timeout",
    "guest_test_prefix",
    "expected_hsa_interrupt",
    "gem5_binary",
    "gem5_config_args",
    "output_dir",
    "artifact_dir",
    "artifact_dir_pattern",
    "matrix_path",
    "provenance_file",
    "guest_bridge_policy",
    "status",
}
TOP_MATRIX_COLUMNS = {
    "row_id",
    "program",
    "program_source",
    "source_sha256",
    "hsa_interrupt",
    "run",
    "session_id",
    "outcome",
    "exit_code",
    "reason",
    "artifact_dir",
    "verdict_artifact",
    "qemu_log",
    "gem5_log",
    "cleanup_status",
    "gem5_source_commit",
    "gem5_binary",
    "gem5_sha256",
    "test_binary",
    "test_binary_sha256",
    "source_fingerprint",
    "strict_acceptance",
    "mode",
    "repeat_count",
    "timeout_policy",
    "boot_timeout",
    "test_timeout",
    "guest_run_timeout",
    "guest_test_prefix",
    "gem5_config_args",
    "artifact_dir_pattern",
    "guest_bridge_policy",
}
LOCAL_MATRIX_COLUMNS = {
    "program",
    "hsa_interrupt",
    "run",
    "session_id",
    "outcome",
    "exit_code",
    "reason",
    "artifact_dir",
    "strict_acceptance",
    "boot_timeout",
    "test_timeout",
    "guest_run_timeout",
}

Error = Dict[str, str]


def _add_error(errors: List[Error], code: str, detail: str) -> None:
    errors.append({"code": code, "detail": detail})


def _normalized_errors(errors: Sequence[Error]) -> List[Error]:
    unique = {(error["code"], error["detail"]) for error in errors}
    return [
        {"code": code, "detail": detail}
        for code, detail in sorted(unique)
    ]


def _resolve_reference(raw: str, repo_root: Path) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = repo_root / path
    return path.resolve()


def _load_tsv(path: Path, required: set[str]) -> List[Dict[str, str]]:
    if not path.is_file():
        raise ValueError(f"missing TSV: {path}")
    with path.open("r", encoding="utf-8", errors="strict", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        fields = reader.fieldnames or []
        if len(fields) != len(set(fields)):
            raise ValueError(f"duplicate TSV column: {path}")
        missing = sorted(required - set(fields))
        if missing:
            raise ValueError(f"TSV lacks columns {','.join(missing)}: {path}")
        rows: List[Dict[str, str]] = []
        for line_number, row in enumerate(reader, 2):
            if None in row:
                raise ValueError(f"extra TSV fields at {path}:{line_number}")
            normalized = {
                key: (value or "").strip() for key, value in row.items()
            }
            if not any(normalized.values()):
                continue
            normalized["__line__"] = str(line_number)
            rows.append(normalized)
    return rows


def _read_key_values(path: Path, errors: List[Error], role: str) -> Dict[str, str]:
    values: Dict[str, str] = {}
    try:
        with path.open("r", encoding="utf-8", errors="strict") as handle:
            for line_number, raw_line in enumerate(handle, 1):
                line = raw_line.rstrip("\r\n")
                if not line or line.lstrip().startswith("#"):
                    continue
                if "=" not in line:
                    _add_error(
                        errors,
                        "invalid_key_value_line",
                        f"{role}:{line_number}",
                    )
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if not key:
                    _add_error(
                        errors,
                        "invalid_key_value_line",
                        f"{role}:{line_number}",
                    )
                    continue
                if key in values:
                    _add_error(errors, "duplicate_key", f"{role}:{key}")
                values[key] = value.strip()
    except (OSError, UnicodeError) as error:
        _add_error(errors, "file_read_error", f"{role}:{error}")
    return values


def _load_json(path: Path, errors: List[Error], role: str) -> Mapping[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        _add_error(errors, "json_read_error", f"{role}:{error}")
        return {}
    if not isinstance(payload, dict):
        _add_error(errors, "invalid_json_type", f"{role}:expected object")
        return {}
    return payload


def _load_json_value(path: Path, errors: List[Error], role: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        _add_error(errors, "json_read_error", f"{role}:{error}")
        return None


def _command_output(
    errors: List[Error], role: str, argv: Sequence[str]
) -> Optional[str]:
    try:
        completed = subprocess.run(
            argv,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as error:
        _add_error(errors, "command_failed", f"{role}:{error}")
        return None
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"exit={completed.returncode}"
        _add_error(errors, "command_failed", f"{role}:{detail}")
        return None
    return completed.stdout.rstrip("\n")


def _hash_file(path: Path, cache: MutableMapping[Path, str]) -> str:
    canonical = path.resolve()
    if canonical in cache:
        return cache[canonical]
    digest = hashlib.sha256()
    with canonical.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    value = digest.hexdigest()
    cache[canonical] = value
    return value


def _staging_fingerprint(root: Path, cache: MutableMapping[Path, str]) -> str:
    files = sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file()
            and not path.is_symlink()
            and path.relative_to(root).parts[0] != "build"
            and not path.name.startswith(".cosim_guest_run.")
        ),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    digest = hashlib.sha256()
    for path in files:
        relative = path.relative_to(root).as_posix()
        digest.update(f"{_hash_file(path, cache)}  ./{relative}\n".encode())
    return digest.hexdigest()


def _require_file(
    path: Path, errors: List[Error], role: str, *, allow_empty: bool = False
) -> bool:
    if path.is_symlink():
        _add_error(errors, "symlink_not_allowed", f"{role}:{path}")
        return False
    if not path.is_file():
        _add_error(errors, "missing_file", f"{role}:{path}")
        return False
    if not allow_empty and path.stat().st_size == 0:
        _add_error(errors, "empty_file", f"{role}:{path}")
        return False
    return True


def _require_directory(path: Path, errors: List[Error], role: str) -> bool:
    if path.is_symlink():
        _add_error(errors, "symlink_not_allowed", f"{role}:{path}")
        return False
    if not path.is_dir():
        _add_error(errors, "missing_directory", f"{role}:{path}")
        return False
    return True


def _expect_value(
    errors: List[Error], field: str, expected: object, actual: object
) -> None:
    if str(actual) != str(expected):
        _add_error(
            errors,
            "value_mismatch",
            f"{field}:expected={expected!s}:actual={actual!s}",
        )


def _expect_path(
    errors: List[Error], field: str, raw: object, expected: Path, repo_root: Path
) -> None:
    if not isinstance(raw, str) or not raw.strip():
        _add_error(errors, "missing_path", field)
        return
    actual = _resolve_reference(raw, repo_root)
    if actual != expected.resolve():
        _add_error(
            errors,
            "path_mismatch",
            f"{field}:expected={expected.resolve()}:actual={actual}",
        )


def _nested(payload: Mapping[str, object], *keys: str) -> object:
    current: object = payload
    for key in keys:
        if not isinstance(current, Mapping) or key not in current:
            return None
        current = current[key]
    return current


def _parse_int(value: object) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        text = str(value)
    except Exception:
        return None
    if not re.fullmatch(r"-?[0-9]+", text):
        return None
    return int(text)


def _shell_words(errors: List[Error], role: str, raw: object) -> List[str]:
    try:
        return shlex.split(str(raw or ""), posix=True)
    except ValueError as error:
        _add_error(errors, "invalid_invocation_argv", f"{role}:{error}")
        return []


def _single_option_value(
    errors: List[Error], role: str, words: Sequence[str], option: str
) -> Optional[str]:
    positions = [index for index, word in enumerate(words) if word == option]
    if len(positions) > 1:
        _add_error(errors, "duplicate_invocation_option", f"{role}:{option}")
        return None
    if not positions:
        return None
    index = positions[0]
    if index + 1 >= len(words) or words[index + 1].startswith("--"):
        _add_error(errors, "missing_invocation_option_value", f"{role}:{option}")
        return None
    return words[index + 1]


def _effective_gem5_invocation(
    errors: List[Error],
    passthrough_words: Sequence[str],
    runner_cwd: Path,
    repo_root: Path,
) -> tuple[Path, str]:
    """从 runner 原始 argv 反推 gem5 选择与配置。"""

    values = {
        "--num-gpus": "1",
        "--num-cus": "40",
        "--host-mem": "8G",
        "--vram-size": "16GiB",
        "--gem5-debug": "",
    }
    binary = repo_root / "gem5" / "build" / "VEGA_X86" / "gem5.opt"
    if len(passthrough_words) % 2:
        _add_error(
            errors,
            "invalid_invocation_passthrough",
            f"odd word count:{passthrough_words!r}",
        )
        return binary.resolve(), ""
    for index in range(0, len(passthrough_words), 2):
        option = passthrough_words[index]
        value = passthrough_words[index + 1]
        if not option.startswith("--"):
            _add_error(
                errors,
                "invalid_invocation_passthrough",
                f"expected option at index {index}:{option!r}",
            )
            continue
        if option not in {*values, "--gem5-bin"}:
            _add_error(
                errors,
                "unsupported_acceptance_passthrough",
                f"{option}={value}",
            )
            continue
        if option == "--gem5-bin":
            raw_binary = Path(value)
            binary = raw_binary if raw_binary.is_absolute() else runner_cwd / raw_binary
            lexical_binary = Path(os.path.abspath(binary))
            canonical_binary = Path(
                os.path.abspath(
                    repo_root / "gem5" / "build" / "VEGA_X86" / "gem5.opt"
                )
            )
            if lexical_binary != canonical_binary:
                _add_error(
                    errors,
                    "noncanonical_gem5_binary_argument",
                    f"expected={canonical_binary}:actual={lexical_binary}",
                )
        elif option in values:
            values[option] = value
    for option in (*values, "--gem5-bin"):
        if passthrough_words.count(option) > 1:
            _add_error(
                errors,
                "duplicate_invocation_option",
                f"gem5_config:{option}",
            )
    for option in ("--num-gpus", "--num-cus"):
        if _parse_int(values[option]) is None or int(values[option]) <= 0:
            _add_error(
                errors,
                "invalid_gem5_config_value",
                f"{option}={values[option]}",
            )
    for option in ("--host-mem", "--vram-size"):
        if not MEMORY_SIZE_RE.fullmatch(values[option]):
            _add_error(
                errors,
                "invalid_gem5_config_value",
                f"{option}={values[option]}",
            )
    if values["--gem5-debug"] and not DEBUG_FLAGS_RE.fullmatch(
        values["--gem5-debug"]
    ):
        _add_error(
            errors,
            "invalid_gem5_config_value",
            f"--gem5-debug={values['--gem5-debug']}",
        )
    config = (
        f"defaults:num-gpus={values['--num-gpus']},"
        f"num-cus={values['--num-cus']},"
        f"host-mem={values['--host-mem']},"
        f"vram-size={values['--vram-size']}"
    )
    if values["--gem5-debug"]:
        config += f";debug-flags={values['--gem5-debug']}"
    return binary.resolve(), config


def _validate_sha(
    errors: List[Error], field: str, value: object
) -> Optional[str]:
    text = str(value or "")
    if not SHA256_RE.fullmatch(text):
        _add_error(errors, "invalid_sha256", f"{field}:{text}")
        return None
    return text.lower()


def _validate_commit(errors: List[Error], field: str, value: object) -> None:
    text = str(value or "")
    if not COMMIT_RE.fullmatch(text):
        _add_error(errors, "invalid_commit", f"{field}:{text}")


def _verify_file_hash(
    errors: List[Error],
    field: str,
    path: Path,
    expected: object,
    cache: MutableMapping[Path, str],
) -> None:
    expected_hash = _validate_sha(errors, field, expected)
    if expected_hash is None or not path.is_file():
        return
    try:
        actual = _hash_file(path, cache)
    except OSError as error:
        _add_error(errors, "hash_read_error", f"{field}:{error}")
        return
    if actual != expected_hash:
        _add_error(
            errors,
            "hash_mismatch",
            f"{field}:expected={expected_hash}:actual={actual}",
        )


def _verify_hip_executable(errors: List[Error], path: Path) -> None:
    """检查 Guest 构建 HIP 可执行文件的最小 producer 形态。

    这里只校验格式与目标，不构成密码学构建证明；外围验收合同会分别校验源码、
    编译完成标记和已记录的文件 hash。
    """

    try:
        with path.open("rb") as handle:
            header = handle.read(64)
            markers = {
                b"__CLANG_OFFLOAD_BUNDLE__": False,
                b"hipv4-amdgcn-amd-amdhsa--gfx942": False,
            }
            overlap = max(map(len, markers)) - 1
            for marker in markers:
                if marker in header:
                    markers[marker] = True
            carry = header[-overlap:]
            while not all(markers.values()):
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                window = carry + chunk
                for marker in markers:
                    if marker in window:
                        markers[marker] = True
                carry = window[-overlap:]
    except OSError as error:
        _add_error(errors, "binary_read_error", f"test_binary:{error}")
        return

    if len(header) < 20 or header[:7] != b"\x7fELF\x02\x01\x01":
        _add_error(errors, "invalid_test_binary_format", "expected ELF64 little-endian")
        return
    elf_type = int.from_bytes(header[16:18], "little")
    machine = int.from_bytes(header[18:20], "little")
    if elf_type not in {2, 3} or machine != 62:
        _add_error(
            errors,
            "invalid_test_binary_format",
            f"expected x86-64 executable:type={elf_type}:machine={machine}",
        )
    for marker, present in markers.items():
        if not present:
            _add_error(
                errors,
                "missing_hip_bundle_marker",
                f"test_binary:{marker.decode('ascii')}",
            )


def _verify_archive_contract(
    errors: List[Error],
    patch_dir: Path,
    snapshot: Mapping[str, str],
    *,
    role: str = "repo",
    list_name: str = "repo-untracked-files.txt",
    archive_name: str = "repo-untracked-files.tar",
    hash_key: str = "repo_untracked_archive_sha256",
) -> None:
    list_path = patch_dir / list_name
    archive_path = patch_dir / archive_name
    try:
        listed = [
            line.strip()
            for line in list_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeError):
        return

    archive_hash = snapshot.get(hash_key, "")
    if archive_hash == "none":
        if listed:
            _add_error(
                errors,
                "untracked_archive_missing",
                f"{role}:nonempty {list_name} with archive hash none",
            )
        if archive_path.exists():
            _add_error(
                errors,
                "unexpected_untracked_archive",
                str(archive_path),
            )
        return
    if not SHA256_RE.fullmatch(archive_hash):
        return
    if not archive_path.is_file():
        _add_error(errors, "missing_file", f"repo_untracked_archive:{archive_path}")
        return
    try:
        with tarfile.open(archive_path, mode="r:*") as archive:
            all_members = archive.getmembers()
    except (OSError, tarfile.TarError) as error:
        _add_error(errors, "invalid_untracked_archive", str(error))
        return

    names: List[str] = []
    for member in all_members:
        if not member.isfile():
            _add_error(
                errors,
                "unsupported_archive_member",
                f"{role}:{member.name}:type={member.type!r}",
            )
            continue
        pure = PurePosixPath(member.name)
        canonical = pure.as_posix()
        if pure.is_absolute() or ".." in pure.parts or canonical in {"", "."}:
            _add_error(errors, "unsafe_archive_path", member.name)
            continue
        if canonical != member.name:
            _add_error(
                errors,
                "noncanonical_archive_path",
                f"{member.name!r}->{canonical!r}",
            )
            continue
        names.append(canonical)
    if len(names) != len(set(names)):
        _add_error(errors, "duplicate_archive_member", str(archive_path))
    if sorted(names) != sorted(listed):
        _add_error(
            errors,
            "archive_list_mismatch",
            f"listed={sorted(listed)!r}:archived={sorted(names)!r}",
        )


def _verify_repo_provenance(
    errors: List[Error],
    artifact_dir: Path,
    snapshot: Mapping[str, str],
    repo_root: Path,
    cache: MutableMapping[Path, str],
) -> None:
    required = (
        "head_commit",
        "source_fingerprint",
        "program",
        "runner_sha256",
        "guest_env_helper_sha256",
        "launcher_sha256",
        "build_script_sha256",
        "repo_status_sha256",
        "repo_patch_sha256",
        "repo_untracked_list_sha256",
        "repo_untracked_archive_sha256",
        "gem5_source_commit",
        "gem5_source_fingerprint",
        "gem5_status_sha256",
        "gem5_patch_sha256",
        "gem5_untracked_list_sha256",
        "gem5_untracked_archive_sha256",
        "gem5_build_meta_sha256",
        "gem5_baseline_lock_sha256",
    )
    for key in required:
        if not snapshot.get(key):
            _add_error(errors, "missing_provenance_key", f"source_snapshot:{key}")

    _validate_commit(errors, "source_snapshot.head_commit", snapshot.get("head_commit"))
    _validate_sha(
        errors, "source_snapshot.source_fingerprint", snapshot.get("source_fingerprint")
    )
    _validate_commit(
        errors,
        "source_snapshot.gem5_source_commit",
        snapshot.get("gem5_source_commit"),
    )
    _validate_sha(
        errors,
        "source_snapshot.gem5_source_fingerprint",
        snapshot.get("gem5_source_fingerprint"),
    )

    runner = repo_root / "scripts" / "run_cosim_tests.sh"
    helper = repo_root / "scripts" / "cosim_guest_env.sh"
    launcher = repo_root / "scripts" / "cosim_launch.sh"
    build_script = repo_root / "scripts" / "cosim_build.sh"
    patch_dir = artifact_dir / "patch"
    repo_patch = patch_dir / "repo.patch"
    repo_status = patch_dir / "repo-status.txt"
    untracked_list = patch_dir / "repo-untracked-files.txt"
    gem5_status = patch_dir / "gem5-status.txt"
    gem5_patch = patch_dir / "gem5.patch"
    gem5_untracked_list = patch_dir / "untracked-files.txt"
    gem5_build_meta = patch_dir / "gem5-build-meta.txt"
    gem5_baseline_lock = patch_dir / "gem5-baseline.lock"

    _require_file(runner, errors, "current_runner")
    _require_file(helper, errors, "current_guest_env_helper")
    _require_file(launcher, errors, "current_launcher")
    _require_file(build_script, errors, "current_build_script")
    _require_file(repo_patch, errors, "repo_patch", allow_empty=True)
    _require_file(repo_status, errors, "repo_status", allow_empty=True)
    _require_file(untracked_list, errors, "repo_untracked_list", allow_empty=True)
    _require_file(gem5_status, errors, "gem5_status", allow_empty=True)
    _require_file(gem5_patch, errors, "gem5_patch", allow_empty=True)
    _require_file(
        gem5_untracked_list, errors, "gem5_untracked_list", allow_empty=True
    )
    _require_file(gem5_build_meta, errors, "gem5_build_meta")
    _require_file(gem5_baseline_lock, errors, "gem5_baseline_lock")

    _verify_file_hash(
        errors,
        "source_snapshot.runner_sha256",
        runner,
        snapshot.get("runner_sha256"),
        cache,
    )
    _verify_file_hash(
        errors,
        "source_snapshot.guest_env_helper_sha256",
        helper,
        snapshot.get("guest_env_helper_sha256"),
        cache,
    )
    _verify_file_hash(
        errors,
        "source_snapshot.launcher_sha256",
        launcher,
        snapshot.get("launcher_sha256"),
        cache,
    )
    _verify_file_hash(
        errors,
        "source_snapshot.build_script_sha256",
        build_script,
        snapshot.get("build_script_sha256"),
        cache,
    )
    _verify_file_hash(
        errors,
        "source_snapshot.repo_status_sha256",
        repo_status,
        snapshot.get("repo_status_sha256"),
        cache,
    )
    _verify_file_hash(
        errors,
        "source_snapshot.repo_patch_sha256",
        repo_patch,
        snapshot.get("repo_patch_sha256"),
        cache,
    )
    _verify_file_hash(
        errors,
        "source_snapshot.repo_untracked_list_sha256",
        untracked_list,
        snapshot.get("repo_untracked_list_sha256"),
        cache,
    )

    archive_hash = snapshot.get("repo_untracked_archive_sha256", "")
    if archive_hash != "none":
        archive = patch_dir / "repo-untracked-files.tar"
        _verify_file_hash(
            errors,
            "source_snapshot.repo_untracked_archive_sha256",
            archive,
            archive_hash,
            cache,
        )
    _verify_archive_contract(errors, patch_dir, snapshot)
    for field, path, key in (
        ("source_snapshot.gem5_status_sha256", gem5_status, "gem5_status_sha256"),
        ("source_snapshot.gem5_patch_sha256", gem5_patch, "gem5_patch_sha256"),
        (
            "source_snapshot.gem5_untracked_list_sha256",
            gem5_untracked_list,
            "gem5_untracked_list_sha256",
        ),
        (
            "source_snapshot.gem5_build_meta_sha256",
            gem5_build_meta,
            "gem5_build_meta_sha256",
        ),
        (
            "source_snapshot.gem5_baseline_lock_sha256",
            gem5_baseline_lock,
            "gem5_baseline_lock_sha256",
        ),
    ):
        _verify_file_hash(errors, field, path, snapshot.get(key), cache)
    gem5_archive_hash = snapshot.get("gem5_untracked_archive_sha256", "")
    if gem5_archive_hash != "none":
        _verify_file_hash(
            errors,
            "source_snapshot.gem5_untracked_archive_sha256",
            patch_dir / "untracked-files.tar",
            gem5_archive_hash,
            cache,
        )
    _verify_archive_contract(
        errors,
        patch_dir,
        snapshot,
        role="gem5",
        list_name="untracked-files.txt",
        archive_name="untracked-files.tar",
        hash_key="gem5_untracked_archive_sha256",
    )
    for field in (
        "repo_untracked_archive_sha256",
        "gem5_untracked_archive_sha256",
    ):
        if snapshot.get(field) != "none":
            _add_error(
                errors,
                "acceptance_untracked_archive_not_empty",
                f"{field}={snapshot.get(field, '')}",
            )

    current_head = _command_output(
        errors, "repo_head", ("git", "-C", str(repo_root), "rev-parse", "HEAD")
    )
    if current_head is not None:
        _expect_value(
            errors,
            "source_snapshot.current_head",
            current_head,
            snapshot.get("head_commit", ""),
        )
    current_status = _command_output(
        errors,
        "repo_status",
        (
            "git",
            "-C",
            str(repo_root),
            "-c",
            "status.renames=false",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--ignore-submodules=none",
        ),
    )
    if current_status:
        _add_error(errors, "acceptance_tree_not_clean", "top-level repository")
    for role, path in (
        ("repo_status", repo_status),
        ("repo_patch", repo_patch),
        ("repo_untracked_list", untracked_list),
        ("gem5_status", gem5_status),
        ("gem5_patch", gem5_patch),
        ("gem5_untracked_list", gem5_untracked_list),
    ):
        try:
            if path.stat().st_size != 0:
                _add_error(errors, "acceptance_tree_not_clean", role)
        except OSError:
            pass

    gem5_root = repo_root / "gem5"
    current_gem5_head = _command_output(
        errors,
        "gem5_head",
        ("git", "-C", str(gem5_root), "rev-parse", "HEAD"),
    )
    if current_gem5_head is not None:
        _expect_value(
            errors,
            "source_snapshot.current_gem5_head",
            current_gem5_head,
            snapshot.get("gem5_source_commit", ""),
        )
    current_gem5_status = _command_output(
        errors,
        "gem5_status",
        (
            "git",
            "-C",
            str(gem5_root),
            "-c",
            "status.renames=false",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--ignore-submodules=none",
        ),
    )
    if current_gem5_status:
        _add_error(errors, "acceptance_tree_not_clean", "gem5 repository")
    gitlink = _command_output(
        errors,
        "gem5_gitlink",
        ("git", "-C", str(repo_root), "ls-tree", "HEAD", "--", "gem5"),
    )
    gitlink_parts = (gitlink or "").split()
    if len(gitlink_parts) < 3 or gitlink_parts[0] != "160000":
        _add_error(errors, "invalid_gem5_gitlink", str(gitlink or ""))
    elif current_gem5_head is not None and gitlink_parts[2] != current_gem5_head:
        _add_error(
            errors,
            "gem5_gitlink_mismatch",
            f"gitlink={gitlink_parts[2]}:head={current_gem5_head}",
        )
    current_gem5_fingerprint = _command_output(
        errors,
        "gem5_source_fingerprint",
        (
            "bash",
            "-c",
            'source "$1"; source_fingerprint "$2"',
            "_",
            str(build_script),
            str(gem5_root),
        ),
    )
    if current_gem5_fingerprint is not None:
        if not SHA256_RE.fullmatch(current_gem5_fingerprint):
            _add_error(
                errors,
                "invalid_sha256",
                f"current_gem5_source_fingerprint:{current_gem5_fingerprint}",
            )
        else:
            _expect_value(
                errors,
                "source_snapshot.current_gem5_source_fingerprint",
                current_gem5_fingerprint,
                snapshot.get("gem5_source_fingerprint", ""),
            )


def _verify_verdict_evidence_paths(
    errors: List[Error],
    verdict: Mapping[str, object],
    expected: Mapping[str, Path],
    repo_root: Path,
) -> None:
    evidence = verdict.get("evidence")
    if not isinstance(evidence, Mapping):
        _add_error(errors, "missing_verdict_evidence", "verdict.evidence")
        return
    for role, path in expected.items():
        _expect_path(
            errors,
            f"verdict.evidence.{role}",
            evidence.get(role),
            path,
            repo_root,
        )


def _validate_row(
    manifest: Mapping[str, str],
    top_rows: Sequence[Mapping[str, str]],
    repo_root: Path,
    cache: MutableMapping[Path, str],
) -> Dict[str, object]:
    row_id = manifest.get("row_id", "")
    program = manifest.get("program", "")
    errors: List[Error] = []
    artifact_raw = manifest.get("artifact_dir", "")
    artifact_dir = _resolve_reference(artifact_raw, repo_root) if artifact_raw else None
    result: Dict[str, object] = {
        "artifact_dir": str(artifact_dir) if artifact_dir is not None else None,
        "effective_hsa": None,
        "errors": [],
        "manifest_status": manifest.get("status"),
        "program": program or None,
        "row_id": row_id,
        "run_outcome": None,
        "verification_outcome": "FAIL",
    }

    for field in (
        "row_id",
        "program",
        "program_source",
        "source_sha256",
        "program_binary",
        "runner_argument",
        "strict_acceptance",
        "mode",
        "repeat_count",
        "timeout_policy",
        "boot_timeout",
        "test_timeout",
        "guest_run_timeout",
        "guest_test_prefix",
        "expected_hsa_interrupt",
        "gem5_binary",
        "gem5_config_args",
        "output_dir",
        "artifact_dir",
        "artifact_dir_pattern",
        "matrix_path",
        "provenance_file",
        "guest_bridge_policy",
    ):
        if not manifest.get(field):
            _add_error(errors, "missing_manifest_field", field)

    if not PROGRAM_RE.fullmatch(program):
        _add_error(errors, "invalid_program_name", program)
    canonical_identity = {
        "program_source": f"tests/kernels/{program}.cpp",
        "program_binary": f"tests/build/{program}",
        "runner_argument": program,
    }
    for field, expected in canonical_identity.items():
        actual = manifest.get(field, "")
        if actual != expected:
            _add_error(
                errors,
                "noncanonical_program_identity",
                f"{field}:expected={expected}:actual={actual}",
            )

    if manifest.get("mode") != "pure_test":
        _add_error(errors, "invalid_manifest_mode", manifest.get("mode", ""))
    if manifest.get("strict_acceptance") != "1":
        _add_error(
            errors,
            "strict_acceptance_required",
            manifest.get("strict_acceptance", ""),
        )
    if manifest.get("repeat_count") != "1":
        _add_error(
            errors,
            "invalid_manifest_repeat_count",
            manifest.get("repeat_count", ""),
        )
    for timeout_field in ("boot_timeout", "test_timeout", "guest_run_timeout"):
        timeout_text = manifest.get(timeout_field, "")
        timeout_value = _parse_int(timeout_text)
        if not re.fullmatch(r"[1-9][0-9]*", timeout_text) or \
                timeout_value is None or timeout_value <= 0:
            _add_error(
                errors,
                "invalid_manifest_timeout",
                f"{timeout_field}={manifest.get(timeout_field, '')}",
            )
    timeout_policy = manifest.get("timeout_policy", "")
    expected_timeout_policy = f"fixed-{manifest.get('test_timeout', '')}"
    if timeout_policy != expected_timeout_policy:
        _add_error(
            errors,
            "invalid_timeout_policy",
            f"expected={expected_timeout_policy}:actual={timeout_policy}",
        )
    if manifest.get("artifact_dir_pattern") != "-":
        _add_error(
            errors,
            "invalid_artifact_dir_pattern",
            manifest.get("artifact_dir_pattern", ""),
        )
    if manifest.get("guest_bridge_policy") != "artifact-local":
        _add_error(
            errors,
            "invalid_guest_bridge_policy",
            manifest.get("guest_bridge_policy", ""),
        )

    if artifact_dir is not None:
        artifact_root = (repo_root / "artifacts").resolve()
        try:
            artifact_dir.relative_to(artifact_root)
        except ValueError:
            _add_error(
                errors,
                "artifact_outside_repository",
                f"root={artifact_root}:artifact={artifact_dir}",
            )
        raw_artifact_path = Path(artifact_raw)
        if not raw_artifact_path.is_absolute():
            raw_artifact_path = repo_root / raw_artifact_path
        lexical_artifact = Path(os.path.abspath(raw_artifact_path))
        if lexical_artifact.is_symlink() or lexical_artifact.resolve() != lexical_artifact:
            _add_error(
                errors,
                "artifact_symlink_not_allowed",
                str(lexical_artifact),
            )

    expected_hsa = manifest.get("expected_hsa_interrupt", "")
    if expected_hsa not in {"0", "1"}:
        _add_error(errors, "invalid_hsa", f"manifest:{expected_hsa}")

    prefix_values = set(PREFIX_HSA_RE.findall(manifest.get("guest_test_prefix", "")))
    if prefix_values != {expected_hsa}:
        _add_error(
            errors,
            "hsa_mismatch",
            f"manifest_prefix={sorted(prefix_values)!r}:expected={expected_hsa}",
        )
    canonical_prefix = f"HSA_ENABLE_INTERRUPT={expected_hsa}"
    if manifest.get("guest_test_prefix") != canonical_prefix:
        _add_error(
            errors,
            "noncanonical_guest_prefix",
            f"expected={canonical_prefix}:actual={manifest.get('guest_test_prefix', '')}",
        )

    if len(top_rows) != 1:
        _add_error(
            errors,
            "top_matrix_row_count",
            f"row_id={row_id}:expected=1:actual={len(top_rows)}",
        )
    top = top_rows[0] if top_rows else {}

    if artifact_dir is None or not artifact_dir.is_dir():
        _add_error(errors, "artifact_missing", str(artifact_dir))
        result["errors"] = _normalized_errors(errors)
        return result

    _require_directory(artifact_dir / "staging", errors, "staging")

    _expect_path(
        errors,
        "manifest.output_dir",
        manifest.get("output_dir"),
        artifact_dir,
        repo_root,
    )
    _expect_path(
        errors,
        "manifest.matrix_path",
        manifest.get("matrix_path"),
        artifact_dir / "matrix.tsv",
        repo_root,
    )
    _expect_path(
        errors,
        "manifest.provenance_file",
        manifest.get("provenance_file"),
        artifact_dir / "patch" / "binary-provenance.txt",
        repo_root,
    )

    paths = {
        "verdict": artifact_dir / "verdict.json",
        "matrix": artifact_dir / "matrix.tsv",
        "metadata": artifact_dir / "runner-metadata.txt",
        "runner_invocation": artifact_dir / "runner-invocation.txt",
        "launch_invocation": artifact_dir / "launch-invocation.txt",
        "guest_script": artifact_dir / "guest-run.sh",
        "source_snapshot": artifact_dir / "patch" / "source-snapshot.txt",
        "binary_provenance": artifact_dir / "patch" / "binary-provenance.txt",
        "gem5_build_meta": artifact_dir / "patch" / "gem5-build-meta.txt",
        "gem5_baseline_lock": artifact_dir / "patch" / "gem5-baseline.lock",
        "docker_inspect": artifact_dir / "docker-inspect.json",
        "guest_overlay": artifact_dir / "guest-overlay.json",
        "guest_base_stat": artifact_dir / "guest-base-stat.txt",
        "preflight_json": artifact_dir / "preflight" / "preflight.json",
        "preflight_text": artifact_dir / "preflight" / "preflight.txt",
        "preflight_resources": artifact_dir / "preflight-resources.log",
        "qemu_log": artifact_dir / "qemu.log",
        "gem5_log": artifact_dir / "gem5.log",
        "cleanup": artifact_dir / "cleanup-status.txt",
    }
    present = {
        role: _require_file(path, errors, role) for role, path in paths.items()
    }
    if not all(present.values()):
        result["errors"] = _normalized_errors(errors)
        return result

    try:
        local_rows = _load_tsv(paths["matrix"], LOCAL_MATRIX_COLUMNS)
    except (OSError, UnicodeError, ValueError) as error:
        _add_error(errors, "local_matrix_error", str(error))
        local_rows = []
    if len(local_rows) != 1:
        _add_error(
            errors,
            "local_matrix_row_count",
            f"expected=1:actual={len(local_rows)}",
        )
    local = local_rows[0] if local_rows else {}

    verdict = _load_json(paths["verdict"], errors, "verdict")
    metadata = _read_key_values(paths["metadata"], errors, "runner_metadata")
    invocation = _read_key_values(
        paths["runner_invocation"], errors, "runner_invocation"
    )
    launch_invocation = _read_key_values(
        paths["launch_invocation"], errors, "launch_invocation"
    )
    snapshot = _read_key_values(paths["source_snapshot"], errors, "source_snapshot")
    provenance = _read_key_values(
        paths["binary_provenance"], errors, "binary_provenance"
    )
    gem5_build_meta = _read_key_values(
        paths["gem5_build_meta"], errors, "gem5_build_meta"
    )
    gem5_baseline_lock = _read_key_values(
        paths["gem5_baseline_lock"], errors, "gem5_baseline_lock"
    )
    docker_inspect = _load_json_value(
        paths["docker_inspect"], errors, "docker_inspect"
    )
    guest_overlay = _load_json(
        paths["guest_overlay"], errors, "guest_overlay"
    )
    guest_base_stat = _read_key_values(
        paths["guest_base_stat"], errors, "guest_base_stat"
    )
    preflight = _load_json(paths["preflight_json"], errors, "run_preflight")
    cleanup = _read_key_values(paths["cleanup"], errors, "cleanup_status")

    expected_build_meta_keys = {
        "commit",
        "source_fingerprint_algorithm",
        "source_fingerprint",
        "docker_build_recipe_fingerprint",
        "timestamp",
        "target",
        "binary",
        "binary_sha256",
        "docker_image",
    }
    if set(gem5_build_meta) != expected_build_meta_keys:
        _add_error(
            errors,
            "invalid_gem5_build_metadata",
            "expected="
            f"{sorted(expected_build_meta_keys)!r}:actual={sorted(gem5_build_meta)!r}",
        )
    _validate_sha(
        errors,
        "gem5_build_meta.docker_build_recipe_fingerprint",
        gem5_build_meta.get("docker_build_recipe_fingerprint"),
    )
    if not gem5_build_meta.get("timestamp"):
        _add_error(errors, "invalid_gem5_build_metadata", "missing timestamp")
    if not re.fullmatch(
        r"sha256:[0-9a-f]{64}", gem5_build_meta.get("docker_image", "")
    ):
        _add_error(
            errors,
            "invalid_gem5_build_metadata",
            f"docker_image={gem5_build_meta.get('docker_image', '')}",
        )
    expected_lock_keys = {
        "schema",
        "gem5_commit",
        "source_fingerprint_algorithm",
        "source_fingerprint",
        "binary_sha256",
        "docker_image",
    }
    if set(gem5_baseline_lock) != expected_lock_keys:
        _add_error(
            errors,
            "invalid_gem5_baseline_lock",
            "expected="
            f"{sorted(expected_lock_keys)!r}:actual={sorted(gem5_baseline_lock)!r}",
        )
    _expect_value(
        errors, "gem5_baseline_lock.schema", "1", gem5_baseline_lock.get("schema", "")
    )
    _validate_commit(
        errors,
        "gem5_baseline_lock.gem5_commit",
        gem5_baseline_lock.get("gem5_commit"),
    )
    _validate_sha(
        errors,
        "gem5_baseline_lock.source_fingerprint",
        gem5_baseline_lock.get("source_fingerprint"),
    )
    _validate_sha(
        errors,
        "gem5_baseline_lock.binary_sha256",
        gem5_baseline_lock.get("binary_sha256"),
    )
    if gem5_baseline_lock.get("source_fingerprint_algorithm") != "2" or \
            not re.fullmatch(
                r"sha256:[0-9a-f]{64}",
                gem5_baseline_lock.get("docker_image", ""),
            ):
        _add_error(errors, "invalid_gem5_baseline_lock", "invalid values")

    if not isinstance(docker_inspect, list) or len(docker_inspect) != 1 or \
            not isinstance(docker_inspect[0], Mapping):
        _add_error(errors, "invalid_docker_inspect", "expected one container")
        docker_container: Mapping[str, object] = {}
    else:
        docker_container = docker_inspect[0]
    _expect_value(
        errors,
        "docker_inspect.image",
        gem5_baseline_lock.get("docker_image", ""),
        docker_container.get("Image", ""),
    )
    docker_config = docker_container.get("Config")
    if not isinstance(docker_config, Mapping):
        _add_error(errors, "invalid_docker_inspect", "missing Config")
        docker_config = {}
    _expect_value(
        errors,
        "docker_inspect.config_image",
        "gem5-run:local",
        docker_config.get("Image", ""),
    )

    _expect_value(
        errors,
        "run_preflight.schema",
        "cosim-preflight-v1",
        preflight.get("schema", ""),
    )
    _expect_value(errors, "run_preflight.profile", "run", preflight.get("profile", ""))
    _expect_value(
        errors,
        "run_preflight.repo_root",
        str(repo_root.resolve()),
        preflight.get("repo_root", ""),
    )
    _expect_value(
        errors,
        "run_preflight.overall_status",
        "PASS",
        preflight.get("overall_status", ""),
    )
    if _parse_int(preflight.get("required_failure_count")) != 0:
        _add_error(
            errors,
            "run_preflight_failed",
            f"required_failure_count={preflight.get('required_failure_count')}",
        )
    checks = preflight.get("checks")
    if not isinstance(checks, list):
        _add_error(errors, "invalid_run_preflight", "checks must be a list")
        checks = []
    checks_by_id: Dict[str, List[Mapping[str, object]]] = {}
    for check in checks:
        if not isinstance(check, Mapping):
            _add_error(errors, "invalid_run_preflight", "non-object check")
            continue
        check_id = str(check.get("id", ""))
        checks_by_id.setdefault(check_id, []).append(check)
        if check.get("required") is True and check.get("status") != "PASS":
            _add_error(
                errors,
                "run_preflight_failed",
                f"{check_id}:{check.get('status')}",
            )
    for check_id in ("run.gem5_provenance", "run.qemu_provenance"):
        matching_checks = checks_by_id.get(check_id, [])
        if len(matching_checks) != 1:
            _add_error(
                errors,
                "invalid_run_preflight",
                f"{check_id} count={len(matching_checks)}",
            )
        elif matching_checks[0].get("status") != "PASS" or \
                matching_checks[0].get("required") is not True:
            _add_error(errors, "run_preflight_failed", check_id)

    _expect_value(errors, "top.row_id", row_id, top.get("row_id", ""))
    for field in ("program", "program_source", "source_sha256"):
        expected = manifest.get(field, "")
        _expect_value(errors, f"top.{field}", expected, top.get(field, ""))
    _expect_value(errors, "local.program", program, local.get("program", ""))
    _expect_value(errors, "verdict.program", program, verdict.get("program", ""))
    _expect_value(errors, "metadata.program", program, metadata.get("program", ""))
    _expect_value(errors, "metadata.test", program, metadata.get("test", ""))
    _expect_value(errors, "invocation.program", program, invocation.get("program", ""))
    _expect_value(errors, "snapshot.program", program, snapshot.get("program", ""))

    _expect_value(
        errors,
        "invocation.schema",
        "cosim-runner-invocation/v1",
        invocation.get("schema", ""),
    )
    _expect_value(
        errors,
        "launch_invocation.schema",
        "cosim-launch-invocation/v1",
        launch_invocation.get("schema", ""),
    )
    for role, values in (
        ("runner_invocation", invocation),
        ("launch_invocation", launch_invocation),
    ):
        for field in ("cwd", "argv0", "argv"):
            if not values.get(field):
                _add_error(errors, "missing_invocation_key", f"{role}:{field}")
        if "$'" in values.get("argv", ""):
            _add_error(
                errors,
                "unsupported_bash_ansi_c_quoting",
                f"{role}:argv",
            )
    if "passthrough_args" not in invocation:
        _add_error(
            errors,
            "missing_invocation_key",
            "runner_invocation:passthrough_args",
        )

    runner_words = _shell_words(errors, "runner_invocation.argv", invocation.get("argv"))
    passthrough_words = _shell_words(
        errors,
        "runner_invocation.passthrough_args",
        invocation.get("passthrough_args"),
    )
    launch_words = _shell_words(
        errors, "launch_invocation.argv", launch_invocation.get("argv")
    )
    expected_launch_words = [
        "--share-dir",
        str((artifact_dir / "staging").resolve()),
        "--artifact-dir",
        str(artifact_dir),
        *passthrough_words,
    ]
    if launch_words != expected_launch_words:
        _add_error(
            errors,
            "invocation_argv_mismatch",
            f"launch:expected={expected_launch_words!r}:actual={launch_words!r}",
        )
    canonical_qemu = Path(
        os.path.abspath(
            repo_root / ".local/cosim/qemu/10.1.5/bin/qemu-system-x86_64"
        )
    )
    canonical_disk = Path(
        os.path.abspath(
            repo_root
            / "gem5-resources/src/x86-ubuntu-gpu-ml/disk-image/x86-ubuntu-rocm70"
        )
    )
    canonical_kernel = Path(
        os.path.abspath(
            repo_root
            / "gem5-resources/src/x86-ubuntu-gpu-ml/vmlinux-rocm70"
        )
    )
    for field, expected in (
        ("gem5_docker_image", "gem5-run:local"),
        ("qemu_binary", str(canonical_qemu)),
        ("disk_image", str(canonical_disk)),
        ("kernel", str(canonical_kernel)),
        ("host_cpus", "4"),
        ("gem5_init_timeout", "120"),
    ):
        _expect_value(
            errors,
            f"launch_invocation.{field}",
            expected,
            launch_invocation.get(field, ""),
        )

    if _require_file(canonical_qemu, errors, "qemu_binary") and not os.access(
        canonical_qemu, os.X_OK
    ):
        _add_error(errors, "binary_not_executable", f"qemu_binary:{canonical_qemu}")
    disk_ready = _require_file(canonical_disk, errors, "disk_image")
    _require_file(canonical_kernel, errors, "kernel")

    expected_overlay = Path(
        f"/tmp/cosim-{launch_invocation.get('run_id', '')}.session/guest-overlay.qcow2"
    )
    for field in ("filename", "backing-filename", "full-backing-filename"):
        expected_path = expected_overlay if field == "filename" else canonical_disk
        _expect_path(
            errors,
            f"guest_overlay.{field}",
            guest_overlay.get(field),
            expected_path,
            repo_root,
        )
    _expect_value(
        errors,
        "guest_overlay.format",
        "qcow2",
        guest_overlay.get("format", ""),
    )
    _expect_value(
        errors,
        "guest_overlay.backing_filename_format",
        "raw",
        guest_overlay.get("backing-filename-format", ""),
    )
    _expect_value(
        errors,
        "guest_overlay.dirty_flag",
        False,
        guest_overlay.get("dirty-flag"),
    )
    _expect_value(
        errors,
        "guest_overlay.corrupt",
        False,
        _nested(guest_overlay, "format-specific", "data", "corrupt"),
    )
    if disk_ready:
        disk_size = str(canonical_disk.stat().st_size)
        _expect_value(
            errors,
            "guest_overlay.virtual_size",
            disk_size,
            guest_overlay.get("virtual-size", ""),
        )
        _expect_value(
            errors,
            "guest_base_stat.size",
            disk_size,
            guest_base_stat.get("size", ""),
        )
    if set(guest_base_stat) != {"path", "size", "mtime"}:
        _add_error(
            errors,
            "invalid_guest_base_stat",
            f"keys={sorted(guest_base_stat)!r}",
        )
    _expect_path(
        errors,
        "guest_base_stat.path",
        guest_base_stat.get("path"),
        canonical_disk,
        repo_root,
    )
    current_disk_mtime = _command_output(
        errors,
        "disk_image_mtime",
        ("stat", "-c", "%y", str(canonical_disk)),
    ) if disk_ready else None
    if current_disk_mtime is not None:
        _expect_value(
            errors,
            "guest_base_stat.mtime",
            current_disk_mtime,
            guest_base_stat.get("mtime", ""),
        )

    runner_cwd = Path(invocation.get("cwd", ""))
    launch_cwd = Path(launch_invocation.get("cwd", ""))
    if not runner_cwd.is_absolute() or runner_cwd.resolve() != launch_cwd.resolve():
        _add_error(
            errors,
            "invocation_cwd_mismatch",
            f"runner={runner_cwd}:launcher={launch_cwd}",
        )
    for role, values, expected_script in (
        ("runner", invocation, repo_root / "scripts" / "run_cosim_tests.sh"),
        ("launcher", launch_invocation, repo_root / "scripts" / "cosim_launch.sh"),
    ):
        cwd = Path(values.get("cwd", ""))
        argv0 = Path(values.get("argv0", ""))
        invoked = argv0 if argv0.is_absolute() else cwd / argv0
        if invoked.resolve() != expected_script.resolve():
            _add_error(
                errors,
                "invocation_argv0_mismatch",
                f"{role}:expected={expected_script.resolve()}:actual={invoked.resolve()}",
            )

    positional: List[str] = []
    parsed_passthrough: List[str] = []
    runner_value_options = {
        "--repeat",
        "--session-name",
        "--screen-log",
        "--boot-timeout",
        "--test-timeout",
        "--guest-run-timeout",
        "--output-dir",
    }
    word_index = 0
    while word_index < len(runner_words):
        word = runner_words[word_index]
        if word in {"--all", "--keep-alive"}:
            word_index += 1
        elif word.startswith("-"):
            if word_index + 1 >= len(runner_words):
                _add_error(errors, "missing_invocation_option_value", f"runner:{word}")
                break
            if word not in runner_value_options:
                parsed_passthrough.extend((word, runner_words[word_index + 1]))
            word_index += 2
        else:
            positional.append(word)
            word_index += 1
    if positional != [program]:
        _add_error(
            errors,
            "invocation_program_mismatch",
            f"expected={[program]!r}:actual={positional!r}",
        )
    if parsed_passthrough != passthrough_words:
        _add_error(
            errors,
            "invocation_passthrough_mismatch",
            f"argv={parsed_passthrough!r}:recorded={passthrough_words!r}",
        )
    effective_gem5_binary, effective_gem5_config = _effective_gem5_invocation(
        errors, parsed_passthrough, runner_cwd, repo_root
    )
    canonical_gem5_binary = (
        repo_root / "gem5" / "build" / "VEGA_X86" / "gem5.opt"
    ).resolve()
    if effective_gem5_binary != canonical_gem5_binary:
        _add_error(
            errors,
            "noncanonical_gem5_binary",
            f"expected={canonical_gem5_binary}:actual={effective_gem5_binary}",
        )
    _expect_path(
        errors,
        "manifest.gem5_binary_from_argv",
        manifest.get("gem5_binary"),
        effective_gem5_binary,
        repo_root,
    )
    _expect_value(
        errors,
        "manifest.gem5_config_args_from_argv",
        effective_gem5_config,
        manifest.get("gem5_config_args", ""),
    )
    if "--all" in runner_words or "--repeat" in runner_words or "--keep-alive" in runner_words:
        _add_error(errors, "invalid_leaf_invocation_mode", repr(runner_words))
    raw_output_dir = _single_option_value(
        errors, "runner", runner_words, "--output-dir"
    )
    if raw_output_dir is None:
        _add_error(errors, "missing_invocation_option", "runner:--output-dir")
    else:
        raw_output_path = Path(raw_output_dir)
        if not raw_output_path.is_absolute():
            raw_output_path = runner_cwd / raw_output_path
        if raw_output_path.resolve() != artifact_dir:
            _add_error(
                errors,
                "invocation_argv_mismatch",
                f"runner.--output-dir:{raw_output_path.resolve()}!={artifact_dir}",
            )
    raw_screen_log = _single_option_value(
        errors, "runner", runner_words, "--screen-log"
    )
    if raw_screen_log is not None:
        raw_screen_path = Path(raw_screen_log)
        if not raw_screen_path.is_absolute():
            raw_screen_path = runner_cwd / raw_screen_path
        expected_screen_path = artifact_dir / "qemu.log"
        if raw_screen_path.resolve() != expected_screen_path.resolve():
            _add_error(
                errors,
                "noncanonical_screen_log",
                f"expected={expected_screen_path.resolve()}:actual={raw_screen_path.resolve()}",
            )
    raw_session_name = _single_option_value(
        errors, "runner", runner_words, "--session-name"
    )
    session_name = raw_session_name or "qemu-cosim-tests"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", session_name) or \
            ".." in session_name:
        _add_error(errors, "unsafe_session_name", session_name)
    for option, field, default in (
        ("--boot-timeout", "boot_timeout", "240"),
        ("--test-timeout", "test_timeout", "60"),
        ("--guest-run-timeout", "guest_run_timeout", "1800"),
    ):
        raw_value = _single_option_value(errors, "runner", runner_words, option)
        actual_value = raw_value if raw_value is not None else default
        _expect_value(
            errors,
            f"runner.argv.{field}",
            manifest.get(field, ""),
            actual_value,
        )

    identity = _nested(verdict, "checks", "program_identity")
    if not isinstance(identity, Mapping):
        _add_error(errors, "missing_verdict_check", "program_identity")
        identity = {}
    for field in ("program_source", "program_binary", "runner_argument"):
        expected = manifest.get(field, "")
        if field == "runner_argument":
            expected = manifest.get("runner_argument", program)
        _expect_value(
            errors,
            f"verdict.program_identity.{field}",
            expected,
            identity.get(field, ""),
        )
    _expect_value(
        errors,
        "metadata.program_source",
        manifest.get("program_source", ""),
        metadata.get("program_source", ""),
    )
    _expect_value(
        errors,
        "metadata.program_binary",
        manifest.get("program_binary", ""),
        metadata.get("program_binary", ""),
    )
    _expect_value(
        errors,
        "metadata.runner_argument",
        manifest.get("runner_argument", program),
        metadata.get("runner_argument", ""),
    )
    for field in ("program_source", "program_binary", "runner_argument"):
        _expect_value(
            errors,
            f"invocation.{field}",
            manifest.get(field, ""),
            invocation.get(field, ""),
        )

    for field in (
        "strict_acceptance",
        "mode",
        "repeat_count",
        "boot_timeout",
        "test_timeout",
        "guest_run_timeout",
        "guest_test_prefix",
        "gem5_config_args",
        "artifact_dir_pattern",
        "guest_bridge_policy",
    ):
        expected = manifest.get(field, "")
        _expect_value(errors, f"top.{field}", expected, top.get(field, ""))
        _expect_value(
            errors,
            f"metadata.{field}",
            expected,
            metadata.get(field, ""),
        )
        _expect_value(
            errors,
            f"invocation.{field}",
            expected,
            invocation.get(field, ""),
        )
    _expect_value(
        errors,
        "top.timeout_policy",
        manifest.get("timeout_policy", ""),
        top.get("timeout_policy", ""),
    )
    _expect_value(
        errors,
        "metadata.timeout_policy",
        timeout_policy,
        metadata.get("timeout_policy", ""),
    )
    _expect_value(
        errors,
        "invocation.timeout_policy",
        timeout_policy,
        invocation.get("timeout_policy", ""),
    )
    for field in ("boot_timeout", "test_timeout", "guest_run_timeout"):
        _expect_value(
            errors,
            f"local.{field}",
            manifest.get(field, ""),
            local.get(field, ""),
        )
    _expect_value(
        errors,
        "local.strict_acceptance",
        manifest.get("strict_acceptance", ""),
        local.get("strict_acceptance", ""),
    )

    source_path = _resolve_reference(manifest.get("program_source", ""), repo_root)
    if _require_file(source_path, errors, "program_source"):
        _verify_file_hash(
            errors,
            "manifest.source_sha256",
            source_path,
            manifest.get("source_sha256"),
            cache,
        )
    staged_source: Optional[Path] = None
    try:
        staged_source = artifact_dir / "staging" / source_path.relative_to(
            repo_root / "tests"
        )
    except ValueError:
        if manifest.get("program_source", "").startswith("tests/"):
            staged_source = artifact_dir / "staging" / Path(
                manifest["program_source"]
            ).relative_to("tests")
    if staged_source is None or not _require_file(
        staged_source, errors, "staged_program_source"
    ):
        staged_source = None
    if staged_source is not None:
        _verify_file_hash(
            errors,
            "staged_source_sha256",
            staged_source,
            manifest.get("source_sha256"),
            cache,
        )

    _expect_value(errors, "top.hsa_interrupt", expected_hsa, top.get("hsa_interrupt", ""))
    _expect_value(
        errors, "local.hsa_interrupt", expected_hsa, local.get("hsa_interrupt", "")
    )
    _expect_value(
        errors,
        "metadata.expected_hsa_enable_interrupt",
        expected_hsa,
        metadata.get("expected_hsa_enable_interrupt", ""),
    )
    _expect_value(
        errors,
        "invocation.expected_hsa_interrupt",
        expected_hsa,
        invocation.get("expected_hsa_interrupt", ""),
    )
    metadata_prefix = set(PREFIX_HSA_RE.findall(metadata.get("guest_test_prefix", "")))
    if metadata_prefix != {expected_hsa}:
        _add_error(
            errors,
            "hsa_mismatch",
            f"metadata_prefix={sorted(metadata_prefix)!r}:expected={expected_hsa}",
        )
    prefix_inputs: Dict[str, str] = {}
    for role, values in (
        ("runner_invocation", invocation),
        ("runner_metadata", metadata),
    ):
        if "guest_test_prefix_input" not in values:
            _add_error(
                errors,
                "missing_invocation_key",
                f"{role}:guest_test_prefix_input",
            )
            continue
        raw_input = values.get("guest_test_prefix_input", "")
        prefix_inputs[role] = raw_input
        allowed_inputs = {canonical_prefix}
        if expected_hsa == "0":
            allowed_inputs.add("")
        if raw_input not in allowed_inputs:
            _add_error(
                errors,
                "noncanonical_guest_prefix_input",
                f"{role}:{raw_input}",
            )
    if len(prefix_inputs) == 2 and len(set(prefix_inputs.values())) != 1:
        _add_error(
            errors,
            "guest_prefix_input_mismatch",
            "runner_invocation="
            f"{prefix_inputs['runner_invocation']}:runner_metadata="
            f"{prefix_inputs['runner_metadata']}",
        )
    verdict_hsa = _nested(
        verdict, "checks", "effective_environment", "hsa_enable_interrupt"
    )
    verdict_hsa_ok = _nested(verdict, "checks", "effective_environment", "ok")
    _expect_value(errors, "verdict.effective_hsa", expected_hsa, verdict_hsa)
    if verdict_hsa_ok is not True:
        _add_error(errors, "invalid_verdict_check", "effective_environment")
    result["effective_hsa"] = verdict_hsa

    qemu_hsa: List[str] = []
    qemu_test_timeouts: List[str] = []
    exact_pass_count = 0
    fail_count = 0
    try:
        with paths["qemu_log"].open(
            "r", encoding="utf-8", errors="replace"
        ) as handle:
            for raw_line in handle:
                line = raw_line.rstrip("\r\n")
                match = HSA_RE.fullmatch(line)
                if match:
                    qemu_hsa.append(match.group(1))
                timeout_match = TEST_TIMEOUT_RE.fullmatch(line)
                if timeout_match:
                    qemu_test_timeouts.append(timeout_match.group(1))
                if line == f"[PASS] {program}":
                    exact_pass_count += 1
                if line == "[FAIL]" or line.startswith("[FAIL] "):
                    fail_count += 1
    except OSError as error:
        _add_error(errors, "file_read_error", f"qemu_log:{error}")
    if set(qemu_hsa) != {expected_hsa}:
        _add_error(
            errors,
            "hsa_mismatch",
            f"qemu_log={sorted(set(qemu_hsa))!r}:expected={expected_hsa}",
        )
    expected_test_timeout = manifest.get("test_timeout", "")
    if set(qemu_test_timeouts) != {expected_test_timeout}:
        _add_error(
            errors,
            "timeout_mismatch",
            "qemu_log.test_timeout="
            f"{sorted(set(qemu_test_timeouts))!r}:expected={expected_test_timeout}",
        )
    try:
        guest_script = paths["guest_script"].read_text(
            encoding="utf-8", errors="strict"
        )
    except (OSError, UnicodeError) as error:
        _add_error(errors, "file_read_error", f"guest_script:{error}")
        guest_script = ""
    expected_guest_timeout_line = (
        f"TEST_TIMEOUT_SECS={expected_test_timeout} ./run_tests.sh {program}"
    )
    if expected_guest_timeout_line not in guest_script.splitlines():
        _add_error(
            errors,
            "timeout_mismatch",
            f"guest_script:{expected_guest_timeout_line}",
        )
    compile_tokens = [
        match.group(1)
        for line in guest_script.splitlines()
        if (match := COMPILE_TOKEN_SCRIPT_RE.fullmatch(line))
    ]
    test_tokens = [
        match.group(1)
        for line in guest_script.splitlines()
        if (match := TEST_TOKEN_SCRIPT_RE.fullmatch(line))
    ]
    if len(compile_tokens) != 1 or len(test_tokens) != 1:
        _add_error(
            errors,
            "invalid_guest_completion_token",
            f"compile={compile_tokens!r}:test={test_tokens!r}",
        )
    else:
        try:
            normalized_qemu_log = paths["qemu_log"].read_text(
                encoding="utf-8", errors="replace"
            ).replace("\r", "")
        except OSError:
            normalized_qemu_log = ""
        for role, token in (("compile", compile_tokens[0]), ("test", test_tokens[0])):
            marker = f"__{token}__:0"
            if normalized_qemu_log.splitlines().count(marker) != 1:
                _add_error(
                    errors,
                    "invalid_guest_completion_token",
                    f"{role}:{marker}",
                )

    outcome = verdict.get("outcome", "")
    exit_code = verdict.get("exit_code", "")
    reason = verdict.get("reason", "")
    result["run_outcome"] = outcome or None
    if not outcome or not reason or _parse_int(exit_code) is None:
        _add_error(errors, "invalid_verdict_result", str(paths["verdict"]))
    if outcome != "PASS":
        _add_error(errors, "accepted_outcome_not_pass", str(outcome))
    if _parse_int(exit_code) != 0:
        _add_error(errors, "accepted_exit_nonzero", str(exit_code))
    if reason != "all_acceptance_gates_passed":
        _add_error(errors, "accepted_reason_invalid", str(reason))
    for role, row in (("top", top), ("local", local)):
        _expect_value(errors, f"{role}.outcome", outcome, row.get("outcome", ""))
        _expect_value(errors, f"{role}.exit_code", exit_code, row.get("exit_code", ""))
        _expect_value(errors, f"{role}.reason", reason, row.get("reason", ""))
    _expect_value(
        errors,
        "metadata.test_exit_code",
        exit_code,
        metadata.get("test_exit_code", ""),
    )
    _expect_value(errors, "metadata.exit_code", exit_code, metadata.get("exit_code", ""))
    _expect_value(errors, "top.run", local.get("run", ""), top.get("run", ""))
    if local.get("run", "") != "1" or top.get("run", "") != "1":
        _add_error(
            errors,
            "invalid_run_ordinal",
            f"local={local.get('run', '')}:top={top.get('run', '')}",
        )
    _expect_value(
        errors,
        "top.session_id",
        local.get("session_id", ""),
        top.get("session_id", ""),
    )
    _expect_value(
        errors,
        "metadata.run_id",
        local.get("session_id", ""),
        metadata.get("run_id", ""),
    )
    _expect_value(
        errors,
        "invocation.run_id",
        local.get("session_id", ""),
        invocation.get("run_id", ""),
    )
    _expect_value(
        errors,
        "launch_invocation.run_id",
        local.get("session_id", ""),
        launch_invocation.get("run_id", ""),
    )
    for role, run_value in (
        ("local", local.get("session_id", "")),
        ("top", top.get("session_id", "")),
        ("metadata", metadata.get("run_id", "")),
        ("runner_invocation", invocation.get("run_id", "")),
        ("launch_invocation", launch_invocation.get("run_id", "")),
    ):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", run_value) or \
                ".." in run_value:
            _add_error(errors, "unsafe_run_id", f"{role}:{run_value}")

    if verdict.get("schema") != "cosim-run-verdict/v1":
        _add_error(errors, "invalid_verdict_schema", str(verdict.get("schema")))
    for check in (
        "binary_provenance",
        "cleanup",
        "effective_environment",
        "program_identity",
        "required_evidence",
        "source_snapshot",
    ):
        if _nested(verdict, "checks", check, "ok") is not True:
            _add_error(errors, "invalid_verdict_check", check)
    if outcome == "PASS":
        for check in (
            "compile",
            "markers",
            "simulator_lifetime",
            "test_exit",
            "timeout",
        ):
            if _nested(verdict, "checks", check, "ok") is not True:
                _add_error(errors, "invalid_verdict_check", check)
        if exact_pass_count != 1 or fail_count != 0:
            _add_error(
                errors,
                "guest_result_mismatch",
                f"pass_count={exact_pass_count}:fail_count={fail_count}",
            )
        for field, expected in (
            ("category", "test_pass"),
            ("compile_exit_code", "0"),
            ("test_exit_code", "0"),
            ("exit_code", "0"),
            ("pass_count", "1"),
            ("fail_count", "0"),
        ):
            _expect_value(
                errors,
                f"metadata.{field}",
                expected,
                metadata.get(field, ""),
            )

    _expect_path(errors, "top.artifact_dir", top.get("artifact_dir"), artifact_dir, repo_root)
    _expect_path(
        errors,
        "local.artifact_dir",
        local.get("artifact_dir"),
        artifact_dir,
        repo_root,
    )
    _expect_path(
        errors,
        "verdict.artifact_dir",
        verdict.get("artifact_dir"),
        artifact_dir,
        repo_root,
    )
    _expect_path(
        errors,
        "top.verdict_artifact",
        top.get("verdict_artifact"),
        paths["verdict"],
        repo_root,
    )
    _expect_path(
        errors,
        "top.qemu_log",
        top.get("qemu_log"),
        paths["qemu_log"],
        repo_root,
    )
    _expect_path(
        errors,
        "top.gem5_log",
        top.get("gem5_log"),
        paths["gem5_log"],
        repo_root,
    )
    _expect_path(
        errors,
        "metadata.source_snapshot",
        metadata.get("source_snapshot"),
        paths["source_snapshot"],
        repo_root,
    )
    _expect_path(
        errors,
        "metadata.runner_invocation",
        metadata.get("runner_invocation"),
        paths["runner_invocation"],
        repo_root,
    )
    _expect_path(
        errors,
        "metadata.launch_invocation",
        metadata.get("launch_invocation"),
        paths["launch_invocation"],
        repo_root,
    )
    _expect_path(
        errors,
        "metadata.guest_script",
        metadata.get("guest_script"),
        paths["guest_script"],
        repo_root,
    )
    for field, expected_path in (
        ("output_dir", artifact_dir),
        ("artifact_dir", artifact_dir),
        ("matrix_path", paths["matrix"]),
        ("provenance_file", paths["binary_provenance"]),
    ):
        _expect_path(
            errors,
            f"invocation.{field}",
            invocation.get(field),
            expected_path,
            repo_root,
        )
    _expect_path(
        errors,
        "launch_invocation.artifact_dir",
        launch_invocation.get("artifact_dir"),
        artifact_dir,
        repo_root,
    )
    _expect_path(
        errors,
        "launch_invocation.share_dir",
        launch_invocation.get("share_dir"),
        artifact_dir / "staging",
        repo_root,
    )
    _expect_path(
        errors,
        "invocation.guest_bridge_host",
        invocation.get("guest_bridge_host"),
        artifact_dir / "staging",
        repo_root,
    )
    _expect_path(
        errors,
        "metadata.guest_bridge_host",
        metadata.get("guest_bridge_host"),
        artifact_dir / "staging",
        repo_root,
    )
    _expect_value(
        errors,
        "invocation.guest_bridge_guest",
        "/mnt",
        invocation.get("guest_bridge_guest", ""),
    )
    _expect_value(
        errors,
        "metadata.guest_bridge_guest",
        "/mnt",
        metadata.get("guest_bridge_guest", ""),
    )
    _verify_verdict_evidence_paths(
        errors,
        verdict,
        {
            "binary_provenance": paths["binary_provenance"],
            "gem5_log": paths["gem5_log"],
            "guest_log": paths["qemu_log"],
            "metadata": paths["metadata"],
            "qemu_log": paths["qemu_log"],
            "source_snapshot": paths["source_snapshot"],
        },
        repo_root,
    )

    cleanup_status = metadata.get("cleanup_status", "")
    cleanup_exit = metadata.get("cleanup_exit_code", "")
    _expect_value(errors, "top.cleanup_status", cleanup_status, top.get("cleanup_status", ""))
    _expect_value(
        errors,
        "verdict.cleanup_status",
        cleanup_status,
        _nested(verdict, "checks", "cleanup", "status"),
    )
    _expect_value(
        errors,
        "verdict.cleanup_exit_code",
        cleanup_exit,
        _nested(verdict, "checks", "cleanup", "exit_code"),
    )
    if set(cleanup) != {"result", "primary_category", "secondary_category"}:
        _add_error(
            errors,
            "invalid_cleanup_status",
            f"keys={sorted(cleanup)!r}",
        )
    for field, expected, actual in (
        ("metadata.cleanup_status", "verified", cleanup_status),
        ("metadata.cleanup_exit_code", "0", cleanup_exit),
        ("cleanup.result", "PASS", cleanup.get("result", "")),
        ("cleanup.primary_category", "test_pass", cleanup.get("primary_category", "")),
        (
            "cleanup.secondary_category",
            "none",
            cleanup.get("secondary_category", ""),
        ),
    ):
        if actual != expected:
            _add_error(
                errors,
                "cleanup_not_verified",
                f"{field}:expected={expected}:actual={actual}",
            )
    required_provenance = (
        "gem5_source_commit",
        "gem5_source_subject",
        "gem5_source_fingerprint_algorithm",
        "gem5_source_fingerprint",
        "gem5_binary",
        "gem5_sha256",
        "gem5_build_meta",
        "gem5_build_meta_sha256",
        "gem5_baseline_lock",
        "gem5_baseline_lock_sha256",
        "gem5_docker_image_name",
        "gem5_docker_image",
        "test_binary",
        "test_binary_sha256",
    )
    for key in required_provenance:
        if not provenance.get(key):
            _add_error(errors, "missing_provenance_key", f"binary_provenance:{key}")
    _validate_commit(
        errors, "binary_provenance.gem5_source_commit", provenance.get("gem5_source_commit")
    )
    _expect_value(
        errors,
        "binary_provenance.gem5_source_fingerprint_algorithm",
        "2",
        provenance.get("gem5_source_fingerprint_algorithm", ""),
    )
    _validate_sha(
        errors,
        "binary_provenance.gem5_source_fingerprint",
        provenance.get("gem5_source_fingerprint"),
    )
    gem5_path = _resolve_reference(provenance.get("gem5_binary", ""), repo_root)
    test_binary = _resolve_reference(provenance.get("test_binary", ""), repo_root)
    _expect_path(
        errors,
        "manifest.gem5_binary",
        manifest.get("gem5_binary"),
        gem5_path,
        repo_root,
    )
    canonical_gem5_path = (
        repo_root / "gem5" / "build" / "VEGA_X86" / "gem5.opt"
    ).resolve()
    if gem5_path != canonical_gem5_path:
        _add_error(
            errors,
            "noncanonical_gem5_binary",
            f"expected={canonical_gem5_path}:actual={gem5_path}",
        )
    _expect_path(
        errors,
        "invocation.gem5_binary",
        invocation.get("gem5_binary"),
        gem5_path,
        repo_root,
    )
    _expect_path(
        errors,
        "metadata.gem5_binary",
        metadata.get("gem5_binary"),
        gem5_path,
        repo_root,
    )
    _expect_path(
        errors,
        "launch_invocation.gem5_binary",
        launch_invocation.get("gem5_binary"),
        gem5_path,
        repo_root,
    )
    _expect_value(
        errors,
        "launch_invocation.gem5_config_args",
        manifest.get("gem5_config_args", ""),
        launch_invocation.get("gem5_config_args", ""),
    )
    try:
        container_relative = gem5_path.relative_to(repo_root / "gem5")
    except ValueError:
        _add_error(errors, "gem5_binary_outside_source_tree", str(gem5_path))
    else:
        _expect_value(
            errors,
            "launch_invocation.gem5_container_binary",
            f"/gem5/{container_relative.as_posix()}",
            launch_invocation.get("gem5_container_binary", ""),
        )
    _expect_path(errors, "top.gem5_binary", top.get("gem5_binary"), gem5_path, repo_root)
    _expect_path(errors, "top.test_binary", top.get("test_binary"), test_binary, repo_root)
    expected_test_binary = artifact_dir / "staging" / "build" / Path(
        manifest.get("program_binary", "")
    ).name
    if expected_test_binary.is_symlink():
        _add_error(
            errors,
            "symlink_not_allowed",
            f"test_binary:{expected_test_binary}",
        )
    if test_binary != expected_test_binary.resolve():
        _add_error(
            errors,
            "path_mismatch",
            "binary_provenance.test_binary:"
            f"expected={expected_test_binary.resolve()}:actual={test_binary}",
        )
    canonical_gem5_lexical = (
        repo_root / "gem5" / "build" / "VEGA_X86" / "gem5.opt"
    )
    if canonical_gem5_lexical.is_symlink() or \
            canonical_gem5_lexical.resolve() != canonical_gem5_lexical:
        _add_error(
            errors,
            "symlink_not_allowed",
            f"gem5_binary:{canonical_gem5_lexical}",
        )
    if _require_file(gem5_path, errors, "gem5_binary") and not os.access(
        gem5_path, os.X_OK
    ):
        _add_error(errors, "binary_not_executable", f"gem5_binary:{gem5_path}")
    if _require_file(test_binary, errors, "test_binary"):
        if not os.access(test_binary, os.X_OK):
            _add_error(errors, "binary_not_executable", f"test_binary:{test_binary}")
        _verify_hip_executable(errors, test_binary)
    _verify_file_hash(
        errors,
        "binary_provenance.gem5_sha256",
        gem5_path,
        provenance.get("gem5_sha256"),
        cache,
    )
    _expect_value(
        errors,
        "gem5_build_meta.commit",
        provenance.get("gem5_source_commit", ""),
        gem5_build_meta.get("commit", ""),
    )
    _expect_value(
        errors,
        "gem5_build_meta.source_fingerprint_algorithm",
        provenance.get("gem5_source_fingerprint_algorithm", ""),
        gem5_build_meta.get("source_fingerprint_algorithm", ""),
    )
    _expect_value(
        errors,
        "gem5_build_meta.source_fingerprint",
        provenance.get("gem5_source_fingerprint", ""),
        gem5_build_meta.get("source_fingerprint", ""),
    )
    _expect_value(
        errors, "gem5_build_meta.target", "VEGA_X86", gem5_build_meta.get("target", "")
    )
    _expect_path(
        errors,
        "gem5_build_meta.binary",
        gem5_build_meta.get("binary"),
        gem5_path,
        repo_root,
    )
    _expect_value(
        errors,
        "gem5_build_meta.binary_sha256",
        provenance.get("gem5_sha256", ""),
        gem5_build_meta.get("binary_sha256", ""),
    )
    _expect_value(
        errors,
        "gem5_build_meta.docker_image",
        provenance.get("gem5_docker_image", ""),
        gem5_build_meta.get("docker_image", ""),
    )
    dockerfile = repo_root / "scripts" / "Dockerfile.run"
    if _require_file(dockerfile, errors, "gem5_docker_build_recipe"):
        _verify_file_hash(
            errors,
            "gem5_build_meta.docker_build_recipe_fingerprint",
            dockerfile,
            gem5_build_meta.get("docker_build_recipe_fingerprint"),
            cache,
        )
    for role, values in (
        ("invocation", invocation),
        ("metadata", metadata),
    ):
        _expect_value(
            errors,
            f"{role}.gem5_docker_image_name",
            provenance.get("gem5_docker_image_name", ""),
            values.get("gem5_docker_image_name", ""),
        )
        _expect_value(
            errors,
            f"{role}.gem5_docker_image",
            provenance.get("gem5_docker_image", ""),
            values.get("gem5_docker_image", ""),
        )
    _expect_value(
        errors,
        "binary_provenance.gem5_docker_image_name",
        "gem5-run:local",
        provenance.get("gem5_docker_image_name", ""),
    )
    _expect_path(
        errors,
        "binary_provenance.gem5_build_meta",
        provenance.get("gem5_build_meta"),
        paths["gem5_build_meta"],
        repo_root,
    )
    _verify_file_hash(
        errors,
        "binary_provenance.gem5_build_meta_sha256",
        paths["gem5_build_meta"],
        provenance.get("gem5_build_meta_sha256"),
        cache,
    )
    _expect_path(
        errors,
        "binary_provenance.gem5_baseline_lock",
        provenance.get("gem5_baseline_lock"),
        paths["gem5_baseline_lock"],
        repo_root,
    )
    _verify_file_hash(
        errors,
        "binary_provenance.gem5_baseline_lock_sha256",
        paths["gem5_baseline_lock"],
        provenance.get("gem5_baseline_lock_sha256"),
        cache,
    )
    _expect_path(
        errors,
        "metadata.gem5_baseline_lock",
        metadata.get("gem5_baseline_lock"),
        paths["gem5_baseline_lock"],
        repo_root,
    )
    _expect_value(
        errors,
        "metadata.gem5_baseline_lock_sha256",
        provenance.get("gem5_baseline_lock_sha256", ""),
        metadata.get("gem5_baseline_lock_sha256", ""),
    )
    for field, provenance_field, meta_field in (
        ("gem5_commit", "gem5_source_commit", "commit"),
        ("source_fingerprint", "gem5_source_fingerprint", "source_fingerprint"),
        ("binary_sha256", "gem5_sha256", "binary_sha256"),
        ("docker_image", "gem5_docker_image", "docker_image"),
    ):
        _expect_value(
            errors,
            f"gem5_baseline_lock.{field}.provenance",
            provenance.get(provenance_field, ""),
            gem5_baseline_lock.get(field, ""),
        )
        _expect_value(
            errors,
            f"gem5_baseline_lock.{field}.build_meta",
            gem5_build_meta.get(meta_field, ""),
            gem5_baseline_lock.get(field, ""),
        )
    current_lock = repo_root / "configs" / "cosim" / "gem5-baseline.lock"
    if _require_file(current_lock, errors, "current_gem5_baseline_lock"):
        _verify_file_hash(
            errors,
            "current_gem5_baseline_lock",
            current_lock,
            provenance.get("gem5_baseline_lock_sha256"),
            cache,
        )
    tracked_lock = _command_output(
        errors,
        "tracked_gem5_baseline_lock",
        (
            "git",
            "-C",
            str(repo_root),
            "show",
            "HEAD:configs/cosim/gem5-baseline.lock",
        ),
    )
    if tracked_lock is not None:
        tracked_lock_hash = hashlib.sha256((tracked_lock + "\n").encode()).hexdigest()
        _expect_value(
            errors,
            "tracked_gem5_baseline_lock.sha256",
            provenance.get("gem5_baseline_lock_sha256", ""),
            tracked_lock_hash,
        )
    for field in (
        "gem5_source_commit",
        "gem5_source_fingerprint",
        "gem5_build_meta_sha256",
        "gem5_baseline_lock_sha256",
    ):
        _expect_value(
            errors,
            f"source_snapshot.{field}",
            provenance.get(field, ""),
            snapshot.get(field, ""),
        )
    _verify_file_hash(
        errors,
        "binary_provenance.test_binary_sha256",
        test_binary,
        provenance.get("test_binary_sha256"),
        cache,
    )
    for field in ("gem5_source_commit", "gem5_sha256", "test_binary_sha256"):
        _expect_value(
            errors,
            f"top.{field}",
            provenance.get(field, ""),
            top.get(field, ""),
        )
    for field in ("gem5_source_commit", "gem5_sha256"):
        _expect_value(
            errors,
            f"verdict.provenance.{field}",
            provenance.get(field, ""),
            _nested(verdict, "provenance", field),
        )
    _expect_path(
        errors,
        "verdict.provenance.gem5_binary",
        _nested(verdict, "provenance", "gem5_binary"),
        gem5_path,
        repo_root,
    )

    _verify_repo_provenance(errors, artifact_dir, snapshot, repo_root, cache)
    source_fingerprint = snapshot.get("source_fingerprint", "")
    _expect_value(
        errors,
        "top.source_fingerprint",
        source_fingerprint,
        top.get("source_fingerprint", ""),
    )
    staging = artifact_dir / "staging"
    if not staging.is_dir():
        _add_error(errors, "staging_missing", str(staging))
    else:
        actual_fingerprint = _staging_fingerprint(staging, cache)
        if actual_fingerprint != source_fingerprint.lower():
            _add_error(
                errors,
                "hash_mismatch",
                "source_fingerprint:"
                f"expected={source_fingerprint.lower()}:actual={actual_fingerprint}",
            )

    result["errors"] = _normalized_errors(errors)
    if not result["errors"]:
        result["verification_outcome"] = "PASS"
    return result


def verify_matrix(
    manifest_path: Path, matrix_path: Path, repo_root: Path = REPO_ROOT
) -> Dict[str, object]:
    manifest_path = manifest_path.resolve()
    matrix_path = matrix_path.resolve()
    repo_root = repo_root.resolve()
    global_errors: List[Error] = []
    try:
        manifest_rows = _load_tsv(manifest_path, MANIFEST_COLUMNS)
    except (OSError, UnicodeError, ValueError) as error:
        _add_error(global_errors, "manifest_error", str(error))
        manifest_rows = []
    try:
        matrix_rows = _load_tsv(matrix_path, TOP_MATRIX_COLUMNS)
    except (OSError, UnicodeError, ValueError) as error:
        _add_error(global_errors, "matrix_error", str(error))
        matrix_rows = []

    manifest_ids: Dict[str, List[Mapping[str, str]]] = {}
    accepted: List[Mapping[str, str]] = []
    ignored: List[Dict[str, str]] = []
    status_by_id: Dict[str, str] = {}
    for row in manifest_rows:
        row_id = row.get("row_id", "")
        status = row.get("status", "")
        if not row_id:
            _add_error(
                global_errors,
                "missing_manifest_row_id",
                f"line={row.get('__line__', '')}",
            )
            continue
        manifest_ids.setdefault(row_id, []).append(row)
        status_by_id[row_id] = status
        if status == "accepted":
            accepted.append(row)
        elif status.startswith("superseded"):
            ignored.append({"row_id": row_id, "status": status})
        else:
            _add_error(
                global_errors,
                "unsupported_manifest_status",
                f"row_id={row_id}:status={status}",
            )
    for row_id, rows in manifest_ids.items():
        if len(rows) != 1:
            _add_error(
                global_errors,
                "duplicate_manifest_row",
                f"row_id={row_id}:count={len(rows)}",
            )

    artifact_ids: Dict[Path, List[str]] = {}
    for row in accepted:
        raw = row.get("artifact_dir", "")
        if raw:
            artifact_ids.setdefault(_resolve_reference(raw, repo_root), []).append(
                row.get("row_id", "")
            )
    duplicate_artifacts = {
        path: rows for path, rows in artifact_ids.items() if len(rows) > 1
    }
    for path, rows in duplicate_artifacts.items():
        _add_error(
            global_errors,
            "duplicate_manifest_artifact",
            f"artifact={path}:rows={','.join(sorted(rows))}",
        )

    matrix_by_id: Dict[str, List[Mapping[str, str]]] = {}
    matrix_artifacts: Dict[Path, List[str]] = {}
    ignored_matrix_rows: List[str] = []
    for row in matrix_rows:
        row_id = row.get("row_id", "")
        if not row_id:
            _add_error(
                global_errors,
                "missing_matrix_row_id",
                f"line={row.get('__line__', '')}",
            )
            continue
        matrix_by_id.setdefault(row_id, []).append(row)
        if status_by_id.get(row_id, "").startswith("superseded"):
            ignored_matrix_rows.append(row_id)
        elif row_id not in manifest_ids:
            _add_error(global_errors, "unexpected_matrix_row", row_id)
        raw_artifact = row.get("artifact_dir", "")
        if raw_artifact:
            matrix_artifacts.setdefault(
                _resolve_reference(raw_artifact, repo_root), []
            ).append(row_id)
    for path, rows in matrix_artifacts.items():
        if len(rows) > 1:
            _add_error(
                global_errors,
                "duplicate_matrix_artifact",
                f"artifact={path}:rows={','.join(sorted(rows))}",
            )

    cache: Dict[Path, str] = {}
    results = [
        _validate_row(row, matrix_by_id.get(row.get("row_id", ""), []), repo_root, cache)
        for row in accepted
    ]
    for result in results:
        artifact = result.get("artifact_dir")
        if artifact and Path(str(artifact)) in duplicate_artifacts:
            errors = list(result.get("errors", []))
            _add_error(errors, "duplicate_manifest_artifact", str(artifact))
            result["errors"] = _normalized_errors(errors)
            result["verification_outcome"] = "FAIL"

    normalized_global = _normalized_errors(global_errors)
    overall = "PASS"
    if normalized_global or any(
        row["verification_outcome"] != "PASS" for row in results
    ):
        overall = "FAIL"
    return {
        "accepted_row_count": len(accepted),
        "errors": normalized_global,
        "ignored_matrix_rows": sorted(set(ignored_matrix_rows)),
        "ignored_rows": ignored,
        "manifest": str(manifest_path),
        "matrix": str(matrix_path),
        "matrix_row_count": len(matrix_rows),
        "outcome": overall,
        "repo_root": str(repo_root),
        "rows": results,
        "schema": SCHEMA,
    }


def write_json_atomic(path: Path, payload: object) -> None:
    path = path.resolve()
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
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--matrix", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="repository root used to resolve manifest paths",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    result = verify_matrix(args.manifest, args.matrix, args.repo_root)
    try:
        write_json_atomic(args.output, result)
    except OSError as error:
        print(f"failed to write verification output: {error}", file=sys.stderr)
        return 1
    return 0 if result["outcome"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
