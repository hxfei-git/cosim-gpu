#!/usr/bin/env python3
"""校验并归档 Guest 构建与运行 provenance。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence


META_KEYS = (
    "component",
    "schema",
    "build_top_commit",
    "build_script_sha256",
    "provenance_validator_sha256",
    "resources_commit",
    "template_tree",
    "overlay_patch_sha256",
    "guest_lock_sha256",
    "recipe_fingerprint",
    "packer_version",
    "packer_sha256",
    "packer_qemu_plugin_version",
    "packer_qemu_plugin_sha256",
    "ubuntu_iso_url",
    "ubuntu_iso_sha256",
    "amdgpu_dkms_version",
    "rocm_version",
    "kernel_version",
    "qemu_binary_sha256",
    "qemu_img_sha256",
    "m5_sha256",
    "image",
    "image_sha256",
    "image_size",
    "kernel",
    "kernel_sha256",
    "kernel_size",
    "artifacts",
    "timestamp",
)

LOCK_KEYS = (
    "GUEST_LOCK_VERSION",
    "PACKER_VERSION",
    "PACKER_URL",
    "PACKER_SHA256",
    "PACKER_QEMU_PLUGIN_VERSION",
    "PACKER_QEMU_PLUGIN_URL",
    "PACKER_QEMU_PLUGIN_SHA256",
    "UBUNTU_ISO_URL",
    "UBUNTU_ISO_SHA256",
    "ROCM_KEY_FINGERPRINT",
    "ROCM_KEY_SHA256",
    "AMDGPU_DKMS_VERSION",
    "ROCM_VERSION",
    "GUEST_KERNEL",
    "GUEST_KERNEL_PACKAGE_VERSION",
    "GUEST_KERNEL_IMAGE_DEB_URL",
    "GUEST_KERNEL_IMAGE_DEB_SHA256",
    "GUEST_KERNEL_MODULES_DEB_URL",
    "GUEST_KERNEL_MODULES_DEB_SHA256",
    "GUEST_KERNEL_MODULES_EXTRA_DEB_URL",
    "GUEST_KERNEL_MODULES_EXTRA_DEB_SHA256",
    "GUEST_KERNEL_HEADERS_DEB_URL",
    "GUEST_KERNEL_HEADERS_DEB_SHA256",
    "GUEST_KERNEL_HEADERS_GENERIC_DEB_URL",
    "GUEST_KERNEL_HEADERS_GENERIC_DEB_SHA256",
)

SEAL_KEYS = (
    "component",
    "schema",
    "guest_build_meta_sha256",
    "image",
    "image_sha256",
    "image_size",
    "image_device",
    "image_inode",
    "image_mtime_ns",
    "image_ctime_ns",
    "sealed_at",
)

REPORT_KEYS = {
    "schema",
    "run_id",
    "validated_at",
    "guest_build_meta",
    "guest_content_seal",
    "source",
    "toolchain",
    "image",
    "kernel",
    "m5",
}
STAT_KEYS = {"path", "size", "device", "inode", "mtime_ns", "ctime_ns"}
IMAGE_REPORT_KEYS = STAT_KEYS | {"sha256", "validation_method"}
HASHED_STAT_REPORT_KEYS = STAT_KEYS | {"sha256", "validation_method"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ProvenanceError(RuntimeError):
    """Guest provenance 不满足严格合同。"""


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def reject_duplicate_json_keys(
    pairs: Sequence[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"重复 JSON key：{key}")
        result[key] = value
    return result


def require_regular(path: Path, role: str) -> Path:
    try:
        info = path.lstat()
    except OSError as error:
        raise ProvenanceError(f"{role} 不可访问：{path}: {error}") from error
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ProvenanceError(f"{role} 必须是非 symlink regular file：{path}")
    try:
        return path.resolve(strict=True)
    except OSError as error:
        raise ProvenanceError(f"{role} 无法解析：{path}: {error}") from error


def file_stat(path: Path, role: str) -> dict[str, object]:
    canonical = require_regular(path, role)
    info = canonical.stat()
    return {
        "path": str(canonical),
        "size": info.st_size,
        "device": info.st_dev,
        "inode": info.st_ino,
        "mtime_ns": info.st_mtime_ns,
        "ctime_ns": info.st_ctime_ns,
    }


def read_key_values(
    path: Path, expected_keys: Sequence[str], role: str, *, comments: bool = False
) -> dict[str, str]:
    canonical = require_regular(path, role)
    values: dict[str, str] = {}
    try:
        lines = canonical.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError) as error:
        raise ProvenanceError(f"{role} 无法读取：{error}") from error
    for number, line in enumerate(lines, 1):
        if not line or (comments and line.startswith("#")):
            continue
        if "=" not in line:
            raise ProvenanceError(f"{role}:{number} 不是 key=value")
        key, value = line.split("=", 1)
        if not key or key.strip() != key or not value:
            raise ProvenanceError(f"{role}:{number} 的 key/value 无效")
        if key in values:
            raise ProvenanceError(f"{role} 含重复 key：{key}")
        values[key] = value
    expected = set(expected_keys)
    if set(values) != expected:
        missing = sorted(expected - set(values))
        extra = sorted(set(values) - expected)
        raise ProvenanceError(
            f"{role} schema 不精确：missing={missing!r}; extra={extra!r}"
        )
    return values


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def write_json(path: Path | None, payload: object) -> None:
    data = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    if path is not None:
        atomic_write(path, data)
    sys.stdout.buffer.write(data)


def git_output(repo: Path, *args: str, binary: bool = False) -> str | bytes:
    try:
        completed = subprocess.run(
            ("git", "-C", str(repo), *args),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as error:
        raise ProvenanceError(f"git 无法执行：{error}") from error
    if completed.returncode != 0:
        detail = completed.stderr.decode(errors="replace").strip()
        raise ProvenanceError(f"git {' '.join(args)} 失败：{detail}")
    if binary:
        return completed.stdout
    return completed.stdout.decode("utf-8", errors="strict").strip()


def tracked_file_hash(repo: Path, path: Path, role: str) -> str:
    canonical = require_regular(path, role)
    try:
        relative = canonical.relative_to(repo.resolve(strict=True)).as_posix()
    except ValueError as error:
        raise ProvenanceError(f"{role} 不在仓库内：{canonical}") from error
    git_output(repo, "ls-files", "--error-unmatch", "--", relative)
    try:
        completed = subprocess.run(
            ("git", "-C", str(repo), "diff", "--quiet", "HEAD", "--", relative),
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as error:
        raise ProvenanceError(f"git diff 无法执行：{error}") from error
    if completed.returncode != 0:
        raise ProvenanceError(f"{role} 与 HEAD 不一致：{relative}")
    head_bytes = git_output(repo, "show", f"HEAD:{relative}", binary=True)
    assert isinstance(head_bytes, bytes)
    head_sha = sha256_bytes(head_bytes)
    current_sha = sha256_file(canonical)
    if current_sha != head_sha:
        raise ProvenanceError(f"{role} worktree 与 HEAD blob 哈希不一致")
    return current_sha


def validate_sha(value: str, role: str) -> None:
    if SHA256_RE.fullmatch(value) is None:
        raise ProvenanceError(f"{role} 不是 lowercase SHA-256：{value}")


def validate_metadata(values: Mapping[str, str]) -> None:
    if values["component"] != "guest" or values["schema"] != "2":
        raise ProvenanceError("Guest metadata component/schema 不受支持")
    for key in (
        "build_script_sha256",
        "provenance_validator_sha256",
        "overlay_patch_sha256",
        "guest_lock_sha256",
        "recipe_fingerprint",
        "packer_sha256",
        "packer_qemu_plugin_sha256",
        "ubuntu_iso_sha256",
        "qemu_binary_sha256",
        "qemu_img_sha256",
        "m5_sha256",
        "image_sha256",
        "kernel_sha256",
    ):
        validate_sha(values[key], f"metadata.{key}")
    for key in ("build_top_commit", "resources_commit", "template_tree"):
        if COMMIT_RE.fullmatch(values[key]) is None:
            raise ProvenanceError(f"metadata.{key} 不是 40 位 Git object")
    for key in ("image_size", "kernel_size"):
        if not values[key].isdigit() or int(values[key]) <= 0:
            raise ProvenanceError(f"metadata.{key} 不是正整数")
    try:
        datetime.fromisoformat(values["timestamp"].replace("Z", "+00:00"))
    except ValueError as error:
        raise ProvenanceError("metadata.timestamp 不是 ISO-8601") from error


def validate_lock(values: Mapping[str, str]) -> None:
    if values["GUEST_LOCK_VERSION"] != "1":
        raise ProvenanceError("guest.lock version 不受支持")
    for key, value in values.items():
        if key.endswith("_URL") and not value.startswith("https://"):
            raise ProvenanceError(f"guest.lock {key} 必须使用 HTTPS")
        if key.endswith("_SHA256"):
            validate_sha(value, f"guest.lock.{key}")
    if re.fullmatch(r"[0-9A-F]{40}", values["ROCM_KEY_FINGERPRINT"]) is None:
        raise ProvenanceError("guest.lock ROCM_KEY_FINGERPRINT 无效")


def recipe_fingerprint(items: Sequence[str]) -> str:
    digest = hashlib.sha256()
    for item in items:
        digest.update(item.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def common_context(args: argparse.Namespace) -> dict[str, object]:
    repo = args.repo_root.resolve(strict=True)
    resources = args.resources_dir.resolve(strict=True)
    metadata_path = require_regular(args.metadata, "Guest metadata")
    metadata = read_key_values(metadata_path, META_KEYS, "Guest metadata")
    validate_metadata(metadata)

    image_stat = file_stat(args.image, "Guest image")
    kernel_stat = file_stat(args.kernel, "Guest kernel")
    m5_stat = file_stat(args.m5, "Guest m5")
    qemu_stat = file_stat(args.qemu_bin, "QEMU binary")
    qemu_img_stat = file_stat(args.qemu_img, "qemu-img")
    if metadata["image"] != image_stat["path"]:
        raise ProvenanceError("metadata.image 不等于 canonical Guest image")
    if metadata["kernel"] != kernel_stat["path"]:
        raise ProvenanceError("metadata.kernel 不等于 canonical Guest kernel")
    if int(metadata["image_size"]) != image_stat["size"]:
        raise ProvenanceError("Guest image size 与 metadata 不一致")
    if int(metadata["kernel_size"]) != kernel_stat["size"]:
        raise ProvenanceError("Guest kernel size 与 metadata 不一致")

    top_head = str(git_output(repo, "rev-parse", "HEAD"))
    build_script_sha = tracked_file_hash(
        repo, repo / "scripts/cosim_build.sh", "Guest build script"
    )
    validator_sha = tracked_file_hash(
        repo, repo / "scripts/guest_provenance.py", "Guest provenance validator"
    )
    if metadata["build_top_commit"] != top_head:
        raise ProvenanceError("metadata.build_top_commit 不等于当前 top-level HEAD")
    if metadata["build_script_sha256"] != build_script_sha:
        raise ProvenanceError("metadata.build_script_sha256 不一致")
    if metadata["provenance_validator_sha256"] != validator_sha:
        raise ProvenanceError("metadata.provenance_validator_sha256 不一致")

    tree_line = str(git_output(repo, "ls-tree", "HEAD", "--", "gem5-resources"))
    match = re.fullmatch(
        r"160000 commit ([0-9a-f]{40})\tgem5-resources", tree_line
    )
    if match is None:
        raise ProvenanceError("无法从 top-level HEAD 取得 gem5-resources gitlink")
    resources_gitlink = match.group(1)
    resources_head = str(git_output(resources, "rev-parse", "HEAD"))
    if resources_head != resources_gitlink:
        raise ProvenanceError(
            f"gem5-resources HEAD/gitlink 不一致：{resources_head}/{resources_gitlink}"
        )
    if metadata["resources_commit"] != resources_gitlink:
        raise ProvenanceError("metadata.resources_commit 不等于 top-level gitlink")
    template_tree = str(
        git_output(
            resources,
            "rev-parse",
            f"{resources_gitlink}:src/x86-ubuntu-gpu-ml",
        )
    )
    if metadata["template_tree"] != template_tree:
        raise ProvenanceError("metadata.template_tree 与锁定资源 tree 不一致")

    lock_sha = tracked_file_hash(repo, args.guest_lock, "guest.lock")
    patch_sha = tracked_file_hash(repo, args.guest_patch, "Guest overlay patch")
    lock = read_key_values(args.guest_lock, LOCK_KEYS, "guest.lock", comments=True)
    validate_lock(lock)
    if metadata["guest_lock_sha256"] != lock_sha:
        raise ProvenanceError("metadata.guest_lock_sha256 不一致")
    if metadata["overlay_patch_sha256"] != patch_sha:
        raise ProvenanceError("metadata.overlay_patch_sha256 不一致")

    expected_pairs = {
        "packer_version": lock["PACKER_VERSION"],
        "packer_sha256": lock["PACKER_SHA256"],
        "packer_qemu_plugin_version": lock["PACKER_QEMU_PLUGIN_VERSION"],
        "packer_qemu_plugin_sha256": lock["PACKER_QEMU_PLUGIN_SHA256"],
        "ubuntu_iso_url": lock["UBUNTU_ISO_URL"],
        "ubuntu_iso_sha256": lock["UBUNTU_ISO_SHA256"],
        "amdgpu_dkms_version": lock["AMDGPU_DKMS_VERSION"],
        "rocm_version": lock["ROCM_VERSION"],
        "kernel_version": lock["GUEST_KERNEL"],
    }
    for key, expected in expected_pairs.items():
        if metadata[key] != expected:
            raise ProvenanceError(f"metadata.{key} 与 guest.lock 不一致")

    kernel_sha = sha256_file(Path(str(kernel_stat["path"])))
    m5_sha = sha256_file(Path(str(m5_stat["path"])))
    qemu_sha = sha256_file(Path(str(qemu_stat["path"])))
    qemu_img_sha = sha256_file(Path(str(qemu_img_stat["path"])))
    for role, actual in (
        ("kernel_sha256", kernel_sha),
        ("m5_sha256", m5_sha),
        ("qemu_binary_sha256", qemu_sha),
        ("qemu_img_sha256", qemu_img_sha),
    ):
        if metadata[role] != actual:
            raise ProvenanceError(f"metadata.{role} 与当前文件不一致")

    expected_recipe = recipe_fingerprint(
        (
            "guest-recipe-v2",
            f"build_top_commit={top_head}",
            f"build_script={build_script_sha}",
            f"provenance_validator={validator_sha}",
            f"resources_commit={resources_gitlink}",
            f"template_tree={template_tree}",
            f"overlay_patch={patch_sha}",
            f"m5={m5_sha}",
            f"qemu={qemu_sha}",
            f"qemu_img={qemu_img_sha}",
            f"packer={lock['PACKER_SHA256']}",
            f"packer_plugin={lock['PACKER_QEMU_PLUGIN_SHA256']}",
            f"guest_lock={lock_sha}",
        )
    )
    if metadata["recipe_fingerprint"] != expected_recipe:
        raise ProvenanceError("Guest recipe_fingerprint 无法独立重建")

    return {
        "repo": repo,
        "validated_top_head": top_head,
        "build_top_commit": metadata["build_top_commit"],
        "build_script_sha": build_script_sha,
        "validator_sha": validator_sha,
        "metadata_path": metadata_path,
        "metadata": metadata,
        "metadata_sha": sha256_file(metadata_path),
        "resources_gitlink": resources_gitlink,
        "resources_head": resources_head,
        "template_tree": template_tree,
        "lock_sha": lock_sha,
        "patch_sha": patch_sha,
        "recipe": expected_recipe,
        "image_stat": image_stat,
        "kernel_stat": kernel_stat,
        "kernel_sha": kernel_sha,
        "m5_stat": m5_stat,
        "m5_sha": m5_sha,
        "qemu_sha": qemu_sha,
        "qemu_img_sha": qemu_img_sha,
    }


def seal_values(path: Path) -> dict[str, str]:
    values = read_key_values(path, SEAL_KEYS, "Guest content seal")
    if values["component"] != "guest-content-seal" or values["schema"] != "1":
        raise ProvenanceError("Guest content seal component/schema 不受支持")
    for key in ("guest_build_meta_sha256", "image_sha256"):
        validate_sha(values[key], f"seal.{key}")
    for key in (
        "image_size",
        "image_device",
        "image_inode",
        "image_mtime_ns",
        "image_ctime_ns",
    ):
        if not values[key].isdigit():
            raise ProvenanceError(f"seal.{key} 不是非负整数")
    return values


def write_seal(path: Path, context: Mapping[str, object], image_sha: str) -> None:
    metadata = context["metadata"]
    image = context["image_stat"]
    assert isinstance(metadata, Mapping) and isinstance(image, Mapping)
    items = {
        "component": "guest-content-seal",
        "schema": "1",
        "guest_build_meta_sha256": str(context["metadata_sha"]),
        "image": str(image["path"]),
        "image_sha256": image_sha,
        "image_size": str(image["size"]),
        "image_device": str(image["device"]),
        "image_inode": str(image["inode"]),
        "image_mtime_ns": str(image["mtime_ns"]),
        "image_ctime_ns": str(image["ctime_ns"]),
        "sealed_at": now_utc(),
    }
    data = "".join(f"{key}={items[key]}\n" for key in SEAL_KEYS).encode()
    atomic_write(path, data)


def validate_seal(path: Path, context: Mapping[str, object]) -> dict[str, str]:
    values = seal_values(path)
    metadata = context["metadata"]
    image = context["image_stat"]
    assert isinstance(metadata, Mapping) and isinstance(image, Mapping)
    expected = {
        "guest_build_meta_sha256": str(context["metadata_sha"]),
        "image": str(image["path"]),
        "image_sha256": str(metadata["image_sha256"]),
        "image_size": str(image["size"]),
        "image_device": str(image["device"]),
        "image_inode": str(image["inode"]),
        "image_mtime_ns": str(image["mtime_ns"]),
        "image_ctime_ns": str(image["ctime_ns"]),
    }
    for key, expected_value in expected.items():
        if values[key] != expected_value:
            raise ProvenanceError(f"Guest content seal 漂移：{key}")
    return values


def require_unchanged_stat(before: Mapping[str, object], role: str) -> None:
    current = file_stat(Path(str(before["path"])), role)
    if dict(before) != current:
        raise ProvenanceError(f"{role} 在 provenance 校验期间发生变化")


def provenance_report(
    args: argparse.Namespace,
    context: Mapping[str, object],
    seal: Mapping[str, str],
    validation_method: str,
) -> dict[str, object]:
    # 这是由本机 canonical meta/seal 派生的 snapshot，不是独立签名的 attestation。
    metadata = context["metadata"]
    image = dict(context["image_stat"])
    kernel = dict(context["kernel_stat"])
    m5 = dict(context["m5_stat"])
    assert isinstance(metadata, Mapping)
    image.update(
        sha256=metadata["image_sha256"], validation_method=validation_method
    )
    kernel.update(sha256=context["kernel_sha"], validation_method="full-sha256")
    m5.update(sha256=context["m5_sha"], validation_method="full-sha256")
    return {
        "schema": "cosim-guest-provenance/v2",
        "run_id": args.run_id,
        "validated_at": now_utc(),
        "guest_build_meta": {
            "path": str(context["metadata_path"]),
            "sha256": context["metadata_sha"],
        },
        "guest_content_seal": {
            "path": str(args.seal.resolve(strict=True)),
            "sha256": sha256_file(args.seal.resolve(strict=True)),
        },
        "source": {
            "build_top_commit": context["build_top_commit"],
            "validated_top_head": context["validated_top_head"],
            "build_script_sha256": context["build_script_sha"],
            "provenance_validator_sha256": context["validator_sha"],
            "resources_gitlink": context["resources_gitlink"],
            "resources_head": context["resources_head"],
            "template_tree": context["template_tree"],
            "guest_lock_sha256": context["lock_sha"],
            "overlay_patch_sha256": context["patch_sha"],
            "recipe_fingerprint": context["recipe"],
        },
        "toolchain": {
            "qemu_binary_sha256": context["qemu_sha"],
            "qemu_img_sha256": context["qemu_img_sha"],
        },
        "image": image,
        "kernel": kernel,
        "m5": m5,
    }


def validate_run_id(run_id: str) -> None:
    if RUN_ID_RE.fullmatch(run_id) is None or ".." in run_id:
        raise ProvenanceError(f"run ID 不安全：{run_id}")


def command_verify(args: argparse.Namespace) -> int:
    context = common_context(args)
    seal = validate_seal(args.seal, context)
    image = context["image_stat"]
    assert isinstance(image, Mapping)
    require_unchanged_stat(image, "Guest image")
    report = provenance_report(args, context, seal, "sealed-stat")
    write_json(args.output, report)
    return 0


def command_seal(args: argparse.Namespace) -> int:
    context = common_context(args)
    metadata = context["metadata"]
    assert isinstance(metadata, Mapping)
    before = dict(context["image_stat"])
    expected_sha = args.known_image_sha256
    if expected_sha:
        validate_sha(expected_sha, "--known-image-sha256")
    image_sha = sha256_file(Path(str(before["path"])))
    after = file_stat(Path(str(before["path"])), "Guest image")
    if before != after:
        raise ProvenanceError("Guest image 在完整哈希期间发生变化")
    if args.known_image_sha256:
        if image_sha != expected_sha:
            raise ProvenanceError("最终 Guest image SHA-256 与 builder 预期不一致")
    if image_sha != metadata["image_sha256"]:
        raise ProvenanceError("Guest image SHA-256 与 metadata 不一致")
    require_unchanged_stat(before, "Guest image")
    write_seal(args.seal, context, image_sha)
    require_unchanged_stat(before, "Guest image")
    seal = validate_seal(args.seal, context)
    report = provenance_report(args, context, seal, "full-sha256")
    write_json(args.output, report)
    return 0


def load_expected_report(path: Path) -> Mapping[str, object]:
    require_regular(path, "Guest provenance report")
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8", errors="strict"),
            object_pairs_hook=reject_duplicate_json_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ProvenanceError(f"Guest provenance report 无法解析：{error}") from error
    if not isinstance(payload, dict) or set(payload) != REPORT_KEYS:
        raise ProvenanceError("Guest provenance report top-level schema 不精确")
    if payload.get("schema") != "cosim-guest-provenance/v2":
        raise ProvenanceError("Guest provenance report schema 不受支持")
    exact_nested_keys = {
        "guest_build_meta": {"path", "sha256"},
        "guest_content_seal": {"path", "sha256"},
        "source": {
            "build_top_commit",
            "validated_top_head",
            "build_script_sha256",
            "provenance_validator_sha256",
            "resources_gitlink",
            "resources_head",
            "template_tree",
            "guest_lock_sha256",
            "overlay_patch_sha256",
            "recipe_fingerprint",
        },
        "toolchain": {"qemu_binary_sha256", "qemu_img_sha256"},
        "image": IMAGE_REPORT_KEYS,
        "kernel": HASHED_STAT_REPORT_KEYS,
        "m5": HASHED_STAT_REPORT_KEYS,
    }
    for key, expected_keys in exact_nested_keys.items():
        value = payload.get(key)
        if not isinstance(value, dict) or set(value) != expected_keys:
            raise ProvenanceError(f"Guest provenance report {key} schema 不精确")
    return payload


def command_post_stat(args: argparse.Namespace) -> int:
    expected = load_expected_report(args.expected)
    if expected.get("run_id") != args.run_id:
        raise ProvenanceError("post-stat run ID 与 preflight report 不一致")
    expected_image = expected["image"]
    assert isinstance(expected_image, Mapping)
    current = file_stat(Path(str(expected_image["path"])), "Guest image")
    matches = all(current[key] == expected_image[key] for key in STAT_KEYS)
    report = {
        "schema": "cosim-guest-post-stat/v1",
        "run_id": args.run_id,
        "captured_at": now_utc(),
        "image": current,
        "matches_pre": matches,
    }
    write_json(args.output, report)
    if not matches:
        print("Guest image stat 在运行期间发生变化", file=sys.stderr)
        return 1
    return 0


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--resources-dir", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--seal", type=Path, required=True)
    parser.add_argument("--guest-lock", type=Path, required=True)
    parser.add_argument("--guest-patch", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--kernel", type=Path, required=True)
    parser.add_argument("--m5", type=Path, required=True)
    parser.add_argument("--qemu-bin", type=Path, required=True)
    parser.add_argument("--qemu-img", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify = subparsers.add_parser("verify")
    add_common_arguments(verify)
    seal = subparsers.add_parser("seal")
    add_common_arguments(seal)
    seal.add_argument("--known-image-sha256", default="")
    post = subparsers.add_parser("post-stat")
    post.add_argument("--run-id", required=True)
    post.add_argument("--expected", type=Path, required=True)
    post.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    validate_run_id(args.run_id)
    if args.command == "verify":
        return command_verify(args)
    if args.command == "seal":
        return command_seal(args)
    return command_post_stat(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ProvenanceError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1) from error
