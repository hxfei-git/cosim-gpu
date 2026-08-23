#!/usr/bin/env python3
"""Build compact, source-attributed TSV indexes over cosim artifacts."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import deque
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

try:
    from classify_runs import (
        FAIL_RE,
        classify_artifact,
        discover_artifact_dirs,
        locate_evidence,
        read_key_values,
    )
except ImportError:  # Imported as scripts.cosim_artifact_audit by unit tests.
    from scripts.classify_runs import (  # type: ignore
        FAIL_RE,
        classify_artifact,
        discover_artifact_dirs,
        locate_evidence,
        read_key_values,
    )


TABLE_HEADERS: Dict[str, Tuple[str, ...]] = {
    "evidence_index.tsv": ("source", "role", "size_bytes"),
    "raw_evidence_contract.tsv": (
        "row",
        "role",
        "required",
        "present",
        "source",
    ),
    "row_status.tsv": (
        "row",
        "status",
        "missing_fields",
        "missing_files",
        "inconsistencies",
    ),
    "verdicts.tsv": ("row", "outcome", "reason", "source", "line"),
    "provenance.tsv": ("row", "key", "value", "source", "line"),
    "log_availability.tsv": (
        "row",
        "role",
        "present",
        "source",
        "size_bytes",
    ),
    "filter_coverage.tsv": (
        "row",
        "status",
        "filter_expression",
        "covered_object_range",
        "final_observed_object_range",
        "uncovered_object_count",
        "source",
    ),
    "signals.tsv": (
        "row",
        "kind",
        "confidence",
        "source",
        "line",
        "text",
    ),
    "candidate_events.tsv": (
        "row",
        "kind",
        "confidence",
        "source",
        "line",
        "text",
    ),
    "review_queue.tsv": ("row", "issue", "priority", "source", "detail"),
    "raw_read_plan.tsv": (
        "row",
        "source",
        "start_line",
        "end_line",
        "reason",
    ),
}

CORE_REQUIRED_ROLES = (
    "metadata",
    "guest_log",
    "qemu_log",
    "gem5_log",
    "source_snapshot",
    "binary_provenance",
    "gem5_status",
    "gem5_patch",
)

KNOWN_FATAL_RULES: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("panic", re.compile(r"^(?:gem5\s+)?panic:")),
    ("fatal", re.compile(r"^(?:gem5\s+)?fatal:")),
    ("assertion", re.compile(r"^Assertion .+ failed\.?$")),
    ("simulator_exit", re.compile(r"^\[COSIM_(?:GEM5|QEMU|SIMULATOR|LAUNCHER)_EXIT\]")),
    ("timeout", re.compile(r"^\[(?:COSIM_)?(?:BOOT_|GEM5_INIT_|TEST_)?TIMEOUT\]")),
)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _source(path: Optional[Path], root: Path) -> str:
    if path is None:
        return ""
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())


def _row_name(path: Path, root: Path) -> str:
    relative = _source(path, root)
    return relative or "."


def _write_tsv(path: Path, header: Sequence[str], rows: Iterable[Sequence[object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(header)
        for row in rows:
            writer.writerow(tuple("" if value is None else value for value in row))


def _role_for_file(path: Path) -> str:
    name = path.name
    relative = path.as_posix()
    if name == "verdict.json":
        return "verdict"
    if name.startswith("matrix") and name.endswith(".tsv"):
        return "matrix"
    if name == "metadata.txt":
        return "metadata"
    if name in {"gem5.log"}:
        return "gem5_log"
    if name in {"qemu.log", "qemu-console.log"}:
        return "qemu_log"
    if "/guest/" in relative or name == "guest.log":
        return "guest_log"
    if name == "binary-provenance.txt":
        return "binary_provenance"
    if name == "source-snapshot.txt":
        return "source_snapshot"
    if name == "gem5-status.txt":
        return "gem5_status"
    if name == "gem5.patch":
        return "gem5_patch"
    if name == "untracked-files.txt":
        return "untracked_files"
    if name == "untracked-files.tar":
        return "untracked_archive"
    if path.suffix == ".log":
        return "other_log"
    return "other"


def _read_recorded_verdict(path: Path) -> Tuple[Optional[Dict[str, object]], Optional[str]]:
    if not path.is_file():
        return None, "missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError) as error:
        return None, f"invalid_json:{error}"
    if not isinstance(payload, dict):
        return None, "verdict_not_object"
    outcome = payload.get("outcome")
    reason = payload.get("reason")
    if outcome not in {"PASS", "FAIL"} or not isinstance(reason, str) or not reason:
        return None, "missing_outcome_or_reason"
    return payload, None


def _json_key_lines(path: Path, keys: Sequence[str]) -> Dict[str, int]:
    wanted = set(keys)
    result: Dict[str, int] = {}
    if not path.is_file():
        return result
    expressions = {
        key: re.compile(rf'^\s*"{re.escape(key)}"\s*:') for key in wanted
    }
    for line_number, line in _iter_lines(path):
        for key, expression in expressions.items():
            if key not in result and expression.match(line):
                result[key] = line_number
    return result


def _iter_lines(path: Path) -> Iterator[Tuple[int, str]]:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_number, raw in enumerate(handle, 1):
            yield line_number, raw.rstrip("\n").rstrip("\r")


def _known_signal(line: str) -> Optional[str]:
    if line.startswith("[PASS] "):
        return "pass_marker"
    if FAIL_RE.match(line):
        return "fail_marker"
    if line.startswith("[COSIM_ENV] HSA_ENABLE_INTERRUPT="):
        return "effective_environment"
    if line.startswith("[COSIM_VERDICT]"):
        return "verdict"
    for kind, expression in KNOWN_FATAL_RULES:
        if expression.match(line):
            return kind
    return None


def _log_paths(evidence: Mapping[str, object]) -> List[Tuple[str, Path]]:
    paths: List[Tuple[str, Path]] = []
    seen = set()
    for role in ("guest_log", "qemu_log", "gem5_log"):
        item = evidence[role]
        path = getattr(item, "path")
        if path is not None:
            canonical = path.resolve()
            if canonical not in seen:
                paths.append((role, canonical))
                seen.add(canonical)
    return paths


def _extract_signals_and_tail(
    row: str,
    root: Path,
    log_paths: Sequence[Tuple[str, Path]],
    preserve_tail: bool,
) -> Tuple[List[Tuple[object, ...]], List[Tuple[object, ...]]]:
    signals: List[Tuple[object, ...]] = []
    candidates: List[Tuple[object, ...]] = []
    for role, path in log_paths:
        tail: deque[Tuple[int, str]] = deque(maxlen=20)
        signal_lines = set()
        for line_number, line in _iter_lines(path):
            kind = _known_signal(line)
            if kind is not None:
                signals.append(
                    (row, kind, "high", _source(path, root), line_number, line)
                )
                signal_lines.add(line_number)
            if line.strip():
                tail.append((line_number, line))
        if preserve_tail:
            for line_number, line in tail:
                if line_number in signal_lines:
                    continue
                candidates.append(
                    (
                        row,
                        f"{role}_tail",
                        "low",
                        _source(path, root),
                        line_number,
                        line,
                    )
                )
    return signals, candidates


def _filter_coverage(
    row: str,
    metadata: Mapping[str, str],
    metadata_path: Optional[Path],
    root: Path,
) -> Tuple[object, ...]:
    expression = next(
        (
            metadata[key]
            for key in ("diagnostic_filter", "filter_expression", "debug_filter")
            if metadata.get(key)
        ),
        "",
    )
    covered = metadata.get("covered_object_range", "")
    final = metadata.get("final_observed_object_range", "")
    uncovered = metadata.get("uncovered_object_count", "")
    if not expression:
        status = "not_applicable"
    elif not covered or not final or not re.fullmatch(r"[0-9]+", uncovered):
        status = "coverage_insufficient"
    elif int(uncovered) > 0:
        status = "coverage_insufficient"
    else:
        status = "complete"
    return (
        row,
        status,
        expression,
        covered,
        final,
        uncovered,
        _source(metadata_path, root),
    )


def audit(root: Path, out: Path) -> Dict[str, int]:
    root = root.resolve()
    out = out.resolve()
    if not root.is_dir():
        raise ValueError(f"artifact root is not a directory: {root}")
    if out == root:
        raise ValueError("audit output directory must not equal the raw artifact root")
    out.mkdir(parents=True, exist_ok=True)

    rows_by_table: Dict[str, List[Tuple[object, ...]]] = {
        name: [] for name in TABLE_HEADERS
    }

    for path in sorted(root.rglob("*"), key=lambda item: str(item)):
        if not path.is_file() or _is_within(path.resolve(), out):
            continue
        rows_by_table["evidence_index.tsv"].append(
            (_source(path, root), _role_for_file(path), path.stat().st_size)
        )

    artifact_dirs = [
        path for path in discover_artifact_dirs(root) if not _is_within(path, out)
    ]
    for artifact_dir in artifact_dirs:
        row = _row_name(artifact_dir, root)
        evidence = locate_evidence(artifact_dir)
        metadata, metadata_lines = read_key_values(evidence["metadata"].path)
        derived = classify_artifact(artifact_dir)
        verdict_path = artifact_dir / "verdict.json"
        matrix_path = artifact_dir / "matrix.tsv"
        recorded, verdict_error = _read_recorded_verdict(verdict_path)

        untracked_required = False
        untracked_path = evidence["untracked_files"].path
        if untracked_path is not None and untracked_path.is_file():
            untracked_required = any(
                line.strip()
                for line in untracked_path.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
            )

        missing_files: List[str] = []
        for role in CORE_REQUIRED_ROLES:
            item = evidence[role]
            present = item.present
            rows_by_table["raw_evidence_contract.tsv"].append(
                (row, role, "yes", "yes" if present else "no", _source(item.path, root))
            )
            if not present:
                missing_files.append(role)
        for role, path, required in (
            ("verdict", verdict_path, True),
            ("matrix", matrix_path, True),
            ("untracked_files", untracked_path, False),
            ("untracked_archive", evidence["untracked_archive"].path, untracked_required),
        ):
            present = path is not None and path.is_file()
            if required and role == "untracked_archive":
                present = present and path.stat().st_size > 0
            rows_by_table["raw_evidence_contract.tsv"].append(
                (
                    row,
                    role,
                    "yes" if required else "no",
                    "yes" if present else "no",
                    _source(path, root),
                )
            )
            if required and not present:
                missing_files.append(role)

        required_check = derived["checks"]["required_evidence"]  # type: ignore[index]
        missing_fields = list(required_check["missing"])  # type: ignore[index]
        identity_check = derived["checks"]["program_identity"]  # type: ignore[index]
        if not identity_check["ok"]:  # type: ignore[index]
            missing_fields.append(str(identity_check["reason"]))  # type: ignore[index]

        inconsistencies: List[str] = []
        if verdict_error and verdict_error != "missing":
            inconsistencies.append(verdict_error)
        if recorded is not None and (
            recorded["outcome"] != derived["outcome"]
            or recorded["reason"] != derived["reason"]
        ):
            inconsistencies.append("recorded_verdict_differs_from_reclassification")

        if inconsistencies:
            status = "inconsistent"
        elif missing_files or missing_fields:
            status = "incomplete"
        else:
            status = "complete"
        rows_by_table["row_status.tsv"].append(
            (
                row,
                status,
                ";".join(sorted(set(missing_fields))),
                ";".join(sorted(set(missing_files))),
                ";".join(inconsistencies),
            )
        )

        authoritative = recorded or derived
        verdict_source = verdict_path if recorded is not None else evidence["metadata"].path
        if recorded is not None:
            key_lines = _json_key_lines(verdict_path, ("outcome", "reason"))
            verdict_line = ";".join(
                f"{key}:{key_lines[key]}"
                for key in ("outcome", "reason")
                if key in key_lines
            )
        else:
            verdict_line = metadata_lines.get("category", "")
        rows_by_table["verdicts.tsv"].append(
            (
                row,
                authoritative["outcome"],
                authoritative["reason"],
                _source(verdict_source, root),
                verdict_line,
            )
        )

        provenance, provenance_lines = read_key_values(
            evidence["binary_provenance"].path
        )
        for key in sorted(provenance):
            rows_by_table["provenance.tsv"].append(
                (
                    row,
                    key,
                    provenance[key],
                    _source(evidence["binary_provenance"].path, root),
                    provenance_lines[key],
                )
            )

        for role in ("guest_log", "qemu_log", "gem5_log"):
            item = evidence[role]
            size = item.path.stat().st_size if item.path is not None and item.path.is_file() else ""
            rows_by_table["log_availability.tsv"].append(
                (
                    row,
                    role,
                    "yes" if item.present else "no",
                    _source(item.path, root),
                    size,
                )
            )

        rows_by_table["filter_coverage.tsv"].append(
            _filter_coverage(row, metadata, evidence["metadata"].path, root)
        )

        nonpass = authoritative["outcome"] != "PASS"
        signals, candidates = _extract_signals_and_tail(
            row, root, _log_paths(evidence), preserve_tail=nonpass
        )
        rows_by_table["signals.tsv"].extend(signals)
        rows_by_table["candidate_events.tsv"].extend(candidates)

        if missing_files or missing_fields:
            rows_by_table["review_queue.tsv"].append(
                (
                    row,
                    "evidence_incomplete",
                    "high",
                    _source(evidence["metadata"].path, root),
                    ";".join(sorted(set(missing_files + missing_fields))),
                )
            )
        for inconsistency in inconsistencies:
            rows_by_table["review_queue.tsv"].append(
                (
                    row,
                    "artifact_inconsistency",
                    "high",
                    _source(verdict_path, root),
                    inconsistency,
                )
            )
        coverage = rows_by_table["filter_coverage.tsv"][-1]
        if coverage[1] == "coverage_insufficient":
            rows_by_table["review_queue.tsv"].append(
                (
                    row,
                    "coverage_insufficient",
                    "high",
                    coverage[-1],
                    "diagnostic filter does not prove the final observed range",
                )
            )
        known_failure_signals = [
            signal
            for signal in signals
            if signal[1] not in {"pass_marker", "effective_environment"}
        ]
        if nonpass and not known_failure_signals:
            rows_by_table["review_queue.tsv"].append(
                (
                    row,
                    "nonpass_unclassified",
                    "high",
                    _source(verdict_source, root),
                    "no known failure signal; inspect preserved candidate events",
                )
            )

        windows = set()
        for event in signals:
            line_number = int(event[4])
            windows.add((event[3], max(1, line_number - 5), line_number + 5, event[1]))
        for event in candidates:
            line_number = int(event[4])
            windows.add((event[3], max(1, line_number - 3), line_number + 3, "candidate_event"))
        for source, start, end, reason in sorted(windows):
            rows_by_table["raw_read_plan.tsv"].append(
                (row, source, start, end, reason)
            )

    for name, header in TABLE_HEADERS.items():
        _write_tsv(out / name, header, rows_by_table[name])

    return {
        "artifact_rows": len(artifact_dirs),
        "indexed_files": len(rows_by_table["evidence_index.tsv"]),
        "review_items": len(rows_by_table["review_queue.tsv"]),
        "candidate_events": len(rows_by_table["candidate_events.tsv"]),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path, help="artifact intake root")
    parser.add_argument("--out", required=True, type=Path, help="TSV output directory")
    parser.add_argument("--json", action="store_true", help="emit summary as JSON")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        summary = audit(args.root, args.out)
    except (OSError, ValueError) as error:
        print(f"artifact audit failed: {error}", file=sys.stderr)
        return 2
    if args.json:
        json.dump(summary, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        writer = csv.writer(sys.stdout, delimiter="\t", lineterminator="\n")
        writer.writerow(("artifact_rows", "indexed_files", "review_items", "candidate_events"))
        writer.writerow(
            (
                summary["artifact_rows"],
                summary["indexed_files"],
                summary["review_items"],
                summary["candidate_events"],
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
