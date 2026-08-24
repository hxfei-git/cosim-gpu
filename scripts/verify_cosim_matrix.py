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
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Dict, List, Mapping, MutableMapping, Optional, Sequence


SCHEMA = "cosim-matrix-verification/v1"
REPO_ROOT = Path(__file__).resolve().parents[1]
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40,64}$")
HSA_RE = re.compile(r"^\[COSIM_ENV\] HSA_ENABLE_INTERRUPT=([01])$")
PREFIX_HSA_RE = re.compile(r"(?:^|\s)HSA_ENABLE_INTERRUPT=([01])(?:\s|$)")

MANIFEST_COLUMNS = {
    "row_id",
    "program",
    "program_source",
    "source_sha256",
    "program_binary",
    "runner_argument",
    "guest_test_prefix",
    "expected_hsa_interrupt",
    "gem5_binary",
    "output_dir",
    "artifact_dir",
    "matrix_path",
    "provenance_file",
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
    if not path.is_file():
        _add_error(errors, "missing_file", f"{role}:{path}")
        return False
    if not allow_empty and path.stat().st_size == 0:
        _add_error(errors, "empty_file", f"{role}:{path}")
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


def _verify_archive_contract(
    errors: List[Error], patch_dir: Path, snapshot: Mapping[str, str]
) -> None:
    list_path = patch_dir / "repo-untracked-files.txt"
    archive_path = patch_dir / "repo-untracked-files.tar"
    try:
        listed = [
            line.strip()
            for line in list_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeError):
        return

    archive_hash = snapshot.get("repo_untracked_archive_sha256", "")
    if archive_hash == "none":
        if listed:
            _add_error(
                errors,
                "untracked_archive_missing",
                "nonempty repo-untracked-files.txt with archive hash none",
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
            members = [member for member in archive.getmembers() if member.isfile()]
    except (OSError, tarfile.TarError) as error:
        _add_error(errors, "invalid_untracked_archive", str(error))
        return

    names: List[str] = []
    for member in members:
        pure = PurePosixPath(member.name)
        if pure.is_absolute() or ".." in pure.parts:
            _add_error(errors, "unsafe_archive_path", member.name)
            continue
        names.append(pure.as_posix())
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
        "repo_patch_sha256",
        "repo_untracked_list_sha256",
        "repo_untracked_archive_sha256",
    )
    for key in required:
        if not snapshot.get(key):
            _add_error(errors, "missing_provenance_key", f"source_snapshot:{key}")

    _validate_commit(errors, "source_snapshot.head_commit", snapshot.get("head_commit"))
    _validate_sha(
        errors, "source_snapshot.source_fingerprint", snapshot.get("source_fingerprint")
    )

    runner = repo_root / "scripts" / "run_cosim_tests.sh"
    helper = repo_root / "scripts" / "cosim_guest_env.sh"
    patch_dir = artifact_dir / "patch"
    repo_patch = patch_dir / "repo.patch"
    untracked_list = patch_dir / "repo-untracked-files.txt"

    _require_file(runner, errors, "current_runner")
    _require_file(helper, errors, "current_guest_env_helper")
    _require_file(repo_patch, errors, "repo_patch", allow_empty=True)
    _require_file(untracked_list, errors, "repo_untracked_list", allow_empty=True)

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
        "expected_hsa_interrupt",
        "gem5_binary",
        "output_dir",
        "artifact_dir",
        "matrix_path",
        "provenance_file",
    ):
        if not manifest.get(field):
            _add_error(errors, "missing_manifest_field", field)

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
        "source_snapshot": artifact_dir / "patch" / "source-snapshot.txt",
        "binary_provenance": artifact_dir / "patch" / "binary-provenance.txt",
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
    snapshot = _read_key_values(paths["source_snapshot"], errors, "source_snapshot")
    provenance = _read_key_values(
        paths["binary_provenance"], errors, "binary_provenance"
    )
    cleanup = _read_key_values(paths["cleanup"], errors, "cleanup_status")

    _expect_value(errors, "top.row_id", row_id, top.get("row_id", ""))
    for field in ("program", "program_source", "source_sha256"):
        expected = manifest.get(field, "")
        _expect_value(errors, f"top.{field}", expected, top.get(field, ""))
    _expect_value(errors, "local.program", program, local.get("program", ""))
    _expect_value(errors, "verdict.program", program, verdict.get("program", ""))
    _expect_value(errors, "metadata.program", program, metadata.get("program", ""))
    _expect_value(errors, "metadata.test", program, metadata.get("test", ""))
    _expect_value(errors, "snapshot.program", program, snapshot.get("program", ""))

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
    metadata_prefix = set(PREFIX_HSA_RE.findall(metadata.get("guest_test_prefix", "")))
    if metadata_prefix != {expected_hsa}:
        _add_error(
            errors,
            "hsa_mismatch",
            f"metadata_prefix={sorted(metadata_prefix)!r}:expected={expected_hsa}",
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

    outcome = verdict.get("outcome", "")
    exit_code = verdict.get("exit_code", "")
    reason = verdict.get("reason", "")
    result["run_outcome"] = outcome or None
    if not outcome or not reason or _parse_int(exit_code) is None:
        _add_error(errors, "invalid_verdict_result", str(paths["verdict"]))
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
    if cleanup_status.lower() not in {"ok", "pass", "passed", "success", "verified"}:
        _add_error(errors, "cleanup_not_verified", cleanup_status)
    if _parse_int(cleanup_exit) != 0:
        _add_error(errors, "cleanup_not_verified", f"exit={cleanup_exit}")
    if cleanup.get("result", "").lower() not in {"ok", "pass", "passed", "success"}:
        _add_error(errors, "cleanup_not_verified", f"result={cleanup.get('result', '')}")
    if cleanup.get("secondary_category", "none").lower() not in {"", "none"}:
        _add_error(
            errors,
            "cleanup_not_verified",
            f"secondary={cleanup.get('secondary_category', '')}",
        )
    required_provenance = (
        "gem5_source_commit",
        "gem5_binary",
        "gem5_sha256",
        "test_binary",
        "test_binary_sha256",
    )
    for key in required_provenance:
        if not provenance.get(key):
            _add_error(errors, "missing_provenance_key", f"binary_provenance:{key}")
    _validate_commit(
        errors, "binary_provenance.gem5_source_commit", provenance.get("gem5_source_commit")
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
    _expect_path(errors, "top.gem5_binary", top.get("gem5_binary"), gem5_path, repo_root)
    _expect_path(errors, "top.test_binary", top.get("test_binary"), test_binary, repo_root)
    expected_test_binary = artifact_dir / "staging" / "build" / Path(
        manifest.get("program_binary", "")
    ).name
    if test_binary != expected_test_binary.resolve():
        _add_error(
            errors,
            "path_mismatch",
            "binary_provenance.test_binary:"
            f"expected={expected_test_binary.resolve()}:actual={test_binary}",
        )
    _require_file(gem5_path, errors, "gem5_binary")
    _require_file(test_binary, errors, "test_binary")
    _verify_file_hash(
        errors,
        "binary_provenance.gem5_sha256",
        gem5_path,
        provenance.get("gem5_sha256"),
        cache,
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
