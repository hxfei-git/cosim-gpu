#!/usr/bin/env python3
"""Bounded extractor for Codex JSONL session records."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional


ALLOWED_MESSAGE_ROLES = {"user", "assistant"}
ALLOWED_RESPONSE_TYPES = {"message", "function_call", "function_call_output"}
SECRET_PATTERNS = [
    re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?i)(id_token|access_token|refresh_token|api[_-]?key|authorization)(['\" ]*[:=]['\" ]*)[^,\\s}]+"),
]


def compact(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text.strip())
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(lambda m: m.group(1) + m.group(2) + "[REDACTED]" if m.lastindex and m.lastindex >= 2 else "[REDACTED]", text)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def text_from_content(content: object) -> str:
    if not isinstance(content, list):
        return ""
    parts: List[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        value = item.get("text") or item.get("input_text") or item.get("output_text") or ""
        if value:
            parts.append(str(value))
    return "\n".join(parts)


def iter_records(
    path: Path,
    start: int,
    end: Optional[int],
    limit: int,
    include_tool_output: bool,
) -> Iterable[Dict[str, str]]:
    source = str(path)
    range_end = "" if end is None else str(end)
    with path.open(errors="replace") as handle:
        for line_no, raw in enumerate(handle, 1):
            if line_no < start:
                continue
            if end is not None and line_no > end:
                break
            try:
                item = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if item.get("type") != "response_item":
                continue
            payload = item.get("payload", {})
            if not isinstance(payload, dict):
                continue
            response_type = payload.get("type", "")
            if response_type not in ALLOWED_RESPONSE_TYPES:
                continue
            if response_type == "message":
                role = str(payload.get("role", ""))
                if role not in ALLOWED_MESSAGE_ROLES:
                    continue
                text = text_from_content(payload.get("content", []))
                if not text:
                    continue
                yield {
                    "source_file": source,
                    "range_start": str(start),
                    "range_end": range_end,
                    "line_no": str(line_no),
                    "record_kind": "message",
                    "role_or_tool": role,
                    "text": compact(text, limit),
                }
            elif response_type == "function_call":
                name = str(payload.get("name", ""))
                args = str(payload.get("arguments", ""))
                yield {
                    "source_file": source,
                    "range_start": str(start),
                    "range_end": range_end,
                    "line_no": str(line_no),
                    "record_kind": "function_call",
                    "role_or_tool": name,
                    "text": compact(args, limit),
                }
            elif response_type == "function_call_output":
                if not include_tool_output:
                    continue
                call_id = str(payload.get("call_id", ""))
                output = str(payload.get("output", ""))
                if not output:
                    continue
                yield {
                    "source_file": source,
                    "range_start": str(start),
                    "range_end": range_end,
                    "line_no": str(line_no),
                    "record_kind": "function_call_output",
                    "role_or_tool": call_id,
                    "text": compact(output, limit),
                }


def write_tsv(rows: Iterable[Dict[str, str]], out: Optional[Path]) -> None:
    fields = ["source_file", "range_start", "range_end", "line_no", "record_kind", "role_or_tool", "text"]
    if out is None:
        writer = csv.DictWriter(sys.stdout, fieldnames=fields, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_meta(
    out: Optional[Path],
    session_file: Path,
    start: int,
    end: Optional[int],
    text_limit: int,
    include_tool_output: bool,
) -> None:
    if out is None:
        return
    meta = {
        "session_file": str(session_file),
        "start": start,
        "end": end,
        "text_limit": text_limit,
        "include_tool_output": include_tool_output,
        "output": str(out),
    }
    with out.with_suffix(out.suffix + ".meta.json").open("w") as handle:
        json.dump(meta, handle, indent=2, sort_keys=True)
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-file", required=True, type=Path, help="Exact Codex JSONL session path")
    parser.add_argument("--start", type=int, default=1, help="First JSONL line number to inspect")
    parser.add_argument("--end", type=int, default=None, help="Last JSONL line number to inspect")
    parser.add_argument("--text-limit", type=int, default=700, help="Maximum text per extracted row")
    parser.add_argument("--include-tool-output", action="store_true", help="Include redacted tool output rows")
    parser.add_argument("--out", type=Path, default=None, help="Optional TSV output path")
    args = parser.parse_args()
    rows = iter_records(args.session_file, args.start, args.end, args.text_limit, args.include_tool_output)
    write_tsv(rows, args.out)
    write_meta(args.out, args.session_file, args.start, args.end, args.text_limit, args.include_tool_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
