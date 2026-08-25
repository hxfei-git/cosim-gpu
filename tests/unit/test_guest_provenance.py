"""Guest provenance 的离线合同与负例。"""

from __future__ import annotations

import argparse
import contextlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import guest_provenance as provenance  # noqa: E402


class GuestProvenanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "repo"
        self.resources = self.root / "gem5-resources"
        self.template = self.resources / "src/x86-ubuntu-gpu-ml"
        self.template.mkdir(parents=True)
        self._git(self.resources, "init", "-q")
        self._git_config(self.resources)
        (self.template / "recipe.txt").write_text("fixture\n", encoding="utf-8")
        self._git(self.resources, "add", ".")
        self._git(self.resources, "commit", "-q", "-m", "fixture")
        self.resources_commit = self._git(
            self.resources, "rev-parse", "HEAD", output=True
        )
        self.template_tree = self._git(
            self.resources,
            "rev-parse",
            f"{self.resources_commit}:src/x86-ubuntu-gpu-ml",
            output=True,
        )

        self.lock = self.root / "configs/cosim/guest.lock"
        self.patch = self.root / "scripts/patches/guest.patch"
        self.lock.parent.mkdir(parents=True)
        self.patch.parent.mkdir(parents=True)
        self.lock_values = self._lock_values()
        self.lock.write_text(
            "".join(f"{key}={self.lock_values[key]}\n" for key in provenance.LOCK_KEYS),
            encoding="utf-8",
        )
        self.patch.write_text("fixture patch\n", encoding="utf-8")
        self.builder = self.root / "scripts/cosim_build.sh"
        self.validator = self.root / "scripts/guest_provenance.py"
        self.builder.write_text("fixture builder\n", encoding="utf-8")
        self.validator.write_text("fixture validator\n", encoding="utf-8")

        self._git(self.root, "init", "-q")
        self._git_config(self.root)
        self._git(self.root, "add", "configs", "scripts")
        self._git(
            self.root,
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{self.resources_commit},gem5-resources",
        )
        self._git(self.root, "commit", "-q", "-m", "fixture")

        (self.template / "disk-image").mkdir()
        (self.template / "files").mkdir()
        self.image = self.template / "disk-image/x86-ubuntu-rocm70"
        self.kernel = self.template / "vmlinux-rocm70"
        self.m5 = self.template / "files/m5"
        self.qemu = self.root / ".local/qemu-system-x86_64"
        self.qemu_img = self.root / ".local/qemu-img"
        self.qemu.parent.mkdir(parents=True)
        for path, data in (
            (self.image, b"image-fixture"),
            (self.kernel, b"kernel-fixture"),
            (self.m5, b"m5-fixture"),
            (self.qemu, b"qemu-fixture"),
            (self.qemu_img, b"qemu-img-fixture"),
        ):
            path.write_bytes(data)

        self.metadata = self.root / ".local/build/guest/.cosim-build-meta"
        self.seal = self.root / ".local/build/guest/.cosim-content-seal"
        self.report = self.root / "artifacts/guest-provenance.json"
        self.metadata.parent.mkdir(parents=True)
        self._write_metadata()
        self.args = argparse.Namespace(
            repo_root=self.root,
            resources_dir=self.resources,
            metadata=self.metadata,
            seal=self.seal,
            guest_lock=self.lock,
            guest_patch=self.patch,
            image=self.image,
            kernel=self.kernel,
            m5=self.m5,
            qemu_bin=self.qemu,
            qemu_img=self.qemu_img,
            run_id="unit-test",
            output=self.report,
            known_image_sha256="",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _git_config(repo: Path) -> None:
        GuestProvenanceTest._git(repo, "config", "user.name", "Test")
        GuestProvenanceTest._git(repo, "config", "user.email", "test@example.invalid")

    @staticmethod
    def _git(repo: Path, *arguments: str, output: bool = False) -> str:
        completed = subprocess.run(
            ("git", "-C", str(repo), *arguments),
            check=True,
            stdout=subprocess.PIPE if output else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        return completed.stdout.strip() if output else ""

    @staticmethod
    def _lock_values() -> dict[str, str]:
        values: dict[str, str] = {}
        for index, key in enumerate(provenance.LOCK_KEYS, 1):
            if key.endswith("_URL"):
                values[key] = f"https://example.invalid/{key.lower()}"
            elif key.endswith("_SHA256"):
                values[key] = f"{index:064x}"
            else:
                values[key] = f"value-{index}"
        values["GUEST_LOCK_VERSION"] = "1"
        values["ROCM_KEY_FINGERPRINT"] = "A" * 40
        return values

    def _write_metadata(self) -> None:
        build_top_commit = self._git(self.root, "rev-parse", "HEAD", output=True)
        build_script_sha = provenance.sha256_file(self.builder)
        validator_sha = provenance.sha256_file(self.validator)
        lock_sha = provenance.sha256_file(self.lock)
        patch_sha = provenance.sha256_file(self.patch)
        m5_sha = provenance.sha256_file(self.m5)
        qemu_sha = provenance.sha256_file(self.qemu)
        qemu_img_sha = provenance.sha256_file(self.qemu_img)
        recipe = provenance.recipe_fingerprint(
            (
                "guest-recipe-v2",
                f"build_top_commit={build_top_commit}",
                f"build_script={build_script_sha}",
                f"provenance_validator={validator_sha}",
                f"resources_commit={self.resources_commit}",
                f"template_tree={self.template_tree}",
                f"overlay_patch={patch_sha}",
                f"m5={m5_sha}",
                f"qemu={qemu_sha}",
                f"qemu_img={qemu_img_sha}",
                f"packer={self.lock_values['PACKER_SHA256']}",
                f"packer_plugin={self.lock_values['PACKER_QEMU_PLUGIN_SHA256']}",
                f"guest_lock={lock_sha}",
            )
        )
        values = {
            "component": "guest",
            "schema": "2",
            "build_top_commit": build_top_commit,
            "build_script_sha256": build_script_sha,
            "provenance_validator_sha256": validator_sha,
            "resources_commit": self.resources_commit,
            "template_tree": self.template_tree,
            "overlay_patch_sha256": patch_sha,
            "guest_lock_sha256": lock_sha,
            "recipe_fingerprint": recipe,
            "packer_version": self.lock_values["PACKER_VERSION"],
            "packer_sha256": self.lock_values["PACKER_SHA256"],
            "packer_qemu_plugin_version": self.lock_values[
                "PACKER_QEMU_PLUGIN_VERSION"
            ],
            "packer_qemu_plugin_sha256": self.lock_values[
                "PACKER_QEMU_PLUGIN_SHA256"
            ],
            "ubuntu_iso_url": self.lock_values["UBUNTU_ISO_URL"],
            "ubuntu_iso_sha256": self.lock_values["UBUNTU_ISO_SHA256"],
            "amdgpu_dkms_version": self.lock_values["AMDGPU_DKMS_VERSION"],
            "rocm_version": self.lock_values["ROCM_VERSION"],
            "kernel_version": self.lock_values["GUEST_KERNEL"],
            "qemu_binary_sha256": qemu_sha,
            "qemu_img_sha256": qemu_img_sha,
            "m5_sha256": m5_sha,
            "image": str(self.image.resolve()),
            "image_sha256": provenance.sha256_file(self.image),
            "image_size": str(self.image.stat().st_size),
            "kernel": str(self.kernel.resolve()),
            "kernel_sha256": provenance.sha256_file(self.kernel),
            "kernel_size": str(self.kernel.stat().st_size),
            "artifacts": str(self.root / "artifacts/build"),
            "timestamp": "2026-08-26T00:00:00Z",
        }
        self.metadata.write_text(
            "".join(f"{key}={values[key]}\n" for key in provenance.META_KEYS),
            encoding="utf-8",
        )

    @staticmethod
    def _quiet_call(function, arguments) -> int:
        with open(os.devnull, "w", encoding="utf-8") as sink:
            with contextlib.redirect_stdout(sink):
                return function(arguments)

    def test_seal_hashes_image_once_then_verify_uses_seal(self) -> None:
        original = provenance.sha256_file
        image_hashes = 0

        def counting(path: Path) -> str:
            nonlocal image_hashes
            if Path(path).resolve() == self.image.resolve():
                image_hashes += 1
            return original(path)

        with mock.patch.object(provenance, "sha256_file", side_effect=counting):
            self.assertEqual(self._quiet_call(provenance.command_seal, self.args), 0)
            self.assertEqual(self._quiet_call(provenance.command_verify, self.args), 0)
        self.assertEqual(image_hashes, 1)
        seal = provenance.read_key_values(
            self.seal, provenance.SEAL_KEYS, "Guest content seal"
        )
        self.assertEqual(set(seal), set(provenance.SEAL_KEYS))

    def test_known_hash_cannot_replace_hashing_final_canonical_image(self) -> None:
        self.args.known_image_sha256 = provenance.sha256_file(self.image)
        self.image.write_bytes(b"X" * self.image.stat().st_size)
        with self.assertRaises(provenance.ProvenanceError):
            provenance.command_seal(self.args)

    def test_committed_builder_change_rejects_old_metadata(self) -> None:
        self.builder.write_text("changed builder\n", encoding="utf-8")
        self._git(self.root, "add", "scripts/cosim_build.sh")
        self._git(self.root, "commit", "-q", "-m", "builder change")
        with self.assertRaises(provenance.ProvenanceError):
            provenance.common_context(self.args)

    def test_same_size_and_mtime_replacement_is_rejected(self) -> None:
        self.args.known_image_sha256 = provenance.sha256_file(self.image)
        self._quiet_call(provenance.command_seal, self.args)
        before = self.image.stat()
        replacement = self.image.with_name("replacement")
        replacement.write_bytes(b"X" * before.st_size)
        os.replace(replacement, self.image)
        os.utime(self.image, ns=(before.st_atime_ns, before.st_mtime_ns))
        with self.assertRaises(provenance.ProvenanceError):
            provenance.command_verify(self.args)

    def test_exact_metadata_and_tracked_inputs_fail_closed(self) -> None:
        self.metadata.write_text(
            self.metadata.read_text(encoding="utf-8") + "extra=value\n",
            encoding="utf-8",
        )
        with self.assertRaises(provenance.ProvenanceError):
            provenance.common_context(self.args)
        self._write_metadata()
        self.lock.write_text(
            self.lock.read_text(encoding="utf-8") + "# dirty\n", encoding="utf-8"
        )
        with self.assertRaises(provenance.ProvenanceError):
            provenance.common_context(self.args)

    def test_kernel_and_m5_are_fully_hashed(self) -> None:
        self.args.known_image_sha256 = provenance.sha256_file(self.image)
        self._quiet_call(provenance.command_seal, self.args)
        original_kernel = self.kernel.read_bytes()
        self.kernel.write_bytes(b"K" * self.kernel.stat().st_size)
        with self.assertRaises(provenance.ProvenanceError):
            provenance.command_verify(self.args)
        self.kernel.write_bytes(original_kernel)
        self.m5.write_bytes(b"M" * self.m5.stat().st_size)
        with self.assertRaises(provenance.ProvenanceError):
            provenance.command_verify(self.args)

    def test_expected_report_rejects_nested_duplicate_json_key(self) -> None:
        self.args.known_image_sha256 = provenance.sha256_file(self.image)
        self._quiet_call(provenance.command_seal, self.args)
        text = self.report.read_text(encoding="utf-8")
        self.report.write_text(
            text.replace(
                '"source": {',
                '"source": {"build_top_commit": "forged",',
                1,
            ),
            encoding="utf-8",
        )
        with self.assertRaises(provenance.ProvenanceError):
            provenance.load_expected_report(self.report)


if __name__ == "__main__":
    unittest.main()
