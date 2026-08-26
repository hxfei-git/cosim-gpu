#!/usr/bin/env python3
"""检查双语文档、实验结构、链接与公开命令合同。"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
ZH_DIR = DOCS / "zh"
EN_DIR = DOCS / "en"

REQUIRED_DOCS = {
    "architecture.md",
    "getting-started.md",
    "labs.md",
    "reference.md",
}

LAB_ANCHORS = (
    "lab-pci-bar-mmio",
    "lab-amdgpu-kfd-init",
    "lab-vram-gtt-gart-gpuvm",
    "lab-ring-queue-doorbell",
    "lab-pm4",
    "lab-sdma",
    "lab-fence-ih-msix",
    "lab-hip-dispatch",
)
NAVIGATION_ANCHORS = (*LAB_ANCHORS, "lab-gem5-debug")

ISSUE_SIGNATURES = (
    "Unable to locate a BIOS ROM",
    "PSP load tmr failed",
    "GART translation ... not found",
    "hipMalloc OK",
    "ring 0 test failed (-110)",
    "OOM",
    "PM4 opcode 0",
    "curTick()",
    "Unimplemented PM4ReleaseMem.dataSelect",
    "PM4 packet opcode 0x... not supported",
    "0x0380",
    "ttyS0",
    "OOM killer",
    "Failed to init DRM client: -13",
    "kgd2kfd_device_exit",
)

ROUTED_POLICY_FILES = (
    ROOT / ".agents" / "skills" / "cosim-gpu-build" / "SKILL.md",
    ROOT / ".agents" / "skills" / "cosim-gpu-launch" / "SKILL.md",
    ROOT / ".agents" / "skills" / "cosim-gpu-guest" / "SKILL.md",
    ROOT / ".agents" / "skills" / "cosim-gpu-debug" / "references" /
        "qemu" / "error-setv-pattern.md",
    ROOT / ".agents" / "skills" / "cosim-gpu-test" / "SKILL.md",
    ROOT / ".agents" / "skills" / "cosim-gpu-debug" / "SKILL.md",
    ROOT / ".agents" / "skills" / "cosim-gpu-debug" / "references" /
        "analysis" / "live-wait-state.md",
)

SKILL_MARKDOWN_FILES = tuple(
    sorted((ROOT / ".agents" / "skills").glob("**/*.md"))
)

ROUTE_CONTRACTS = {
    "cosim-gpu-flow-plan": ".agents/skills/cosim-gpu-flow-plan/SKILL.md",
    "cosim-gpu-build": ".agents/skills/cosim-gpu-build/SKILL.md",
    "cosim-gpu-launch": ".agents/skills/cosim-gpu-launch/SKILL.md",
    "cosim-gpu-guest": ".agents/skills/cosim-gpu-guest/SKILL.md",
    "cosim-gpu-test": ".agents/skills/cosim-gpu-test/SKILL.md",
    "cosim-gpu-debug": ".agents/skills/cosim-gpu-debug/SKILL.md",
    "cosim-gpu-rocm-stack": ".agents/skills/cosim-gpu-rocm-stack/SKILL.md",
    "cosim-gpu-disk-image-edit":
        ".agents/skills/cosim-gpu-disk-image-edit/SKILL.md",
    "cosim-gpu-info-gathering":
        ".agents/skills/cosim-gpu-info-gathering/SKILL.md",
    "cosim-gpu-review": ".agents/skills/cosim-gpu-review/SKILL.md",
    "cosim-gpu-codex-review":
        ".agents/skills/cosim-gpu-codex-review/SKILL.md",
    "cosim-gpu-rlcr-loop": ".agents/skills/cosim-gpu-rlcr-loop/SKILL.md",
    "cosim-gpu-repo-maintenance":
        ".agents/skills/cosim-gpu-repo-maintenance/SKILL.md",
}

ZH_LAB_SECTIONS = (
    "原理",
    "三层边界",
    "数据流",
    "对应源码与关键函数",
    "运行方法",
    "Debug 方法",
    "正常现象",
    "可修改实验点",
    "验收 artifact",
    "恢复方法",
)
EN_LAB_SECTIONS = (
    "Principle",
    "Layer boundaries",
    "Data flow",
    "Source and key functions",
    "How to run",
    "Debugging",
    "Expected behavior",
    "Experiments",
    "Acceptance artifacts",
    "Recovery",
)

LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
ANCHOR_RE = re.compile(r"<a\s+(?:id|name)=[\"']([^\"']+)[\"']\s*></a>", re.I)
HEADING_RE = re.compile(r"^(#{1,6})\s+", re.M)
FENCE_RE = re.compile(r"^```([^`]*)$", re.M)
UNSAFE_COMMAND_RE = re.compile(
    r"^(?:sudo\s+)?(?:"
    r"docker\s+(?:build|run)\b|"
    r"scons\b|"
    r"qemu-system\S*\b|"
    r"(?:\./|bash\s+)scripts/run_mi300x_fs\.sh\b|"
    r"dd\b.*\bof=/dev/mem\b|"
    r"(?:modprobe|rmmod)\s+amdgpu\b"
    r")"
)
SECRET_COMMAND_RE = re.compile(r"(?:\bsudo\s+-S\b|COSIM_GUEST_SUDO_PASSWORD)")
DEBUG_OPTION_RE = re.compile(r"--gem5-debug(?:=|\s+)([A-Za-z0-9_,]+)")
OPTION_RE = re.compile(r"(?<![A-Za-z0-9])(--[a-z][a-z0-9-]*)")
CODE_IDENTIFIER_RE = re.compile(
    r"`([A-Za-z_][A-Za-z0-9_]*(?:::[A-Za-z_][A-Za-z0-9_]*)?\*?)`"
)
INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
LOCAL_SOURCE_PATH_RE = re.compile(
    r"(?:gem5|gem5-resources|scripts|configs|tests)/[^`\n]+"
)
SOURCE_PATH_RE = re.compile(rf"`({LOCAL_SOURCE_PATH_RE.pattern})`")
RELATIVE_SOURCE_PATH_RE = re.compile(
    r"^(?:[A-Za-z0-9_+.-]+/)*(?:"
    r"[A-Za-z0-9_+.-]+\.(?:c|cc|cpp|cxx|h|hh|hpp|hxx|inc|py|sh)|"
    r"Makefile|SConscript"
    r")$"
)
IDENTIFIER_TOKEN_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:::[A-Za-z_][A-Za-z0-9_]*)?\*?$"
)
EVIDENCE_CONTRACT_TOKENS = (
    "runner-invocation.txt",
    "launch-invocation.txt",
    "guest-run.sh",
    "cosim-matrix-verification/v2",
)
PHASE6_DOC_PATHS = tuple(
    directory / name
    for directory in (ZH_DIR, EN_DIR)
    for name in sorted(REQUIRED_DOCS)
)
BROAD_KILL_RE = re.compile(
    r"(?m)^\s*(?:(?:sudo\s+)?(?:pkill|killall|docker\s+kill)\b|"
    r"kill\b[^\n]*(?:\bpgrep\b|\bpidof\b))"
)
TERMINATING_KILL_RE = re.compile(
    r"(?m)^\s*kill\s+(?:-(?:TERM|KILL|9)|-s\s+(?:TERM|KILL))\b([^\n]*)"
)


class Contract:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def require(self, condition: bool, message: str) -> None:
        if not condition:
            self.errors.append(message)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def without_fenced_blocks(text: str) -> str:
    """移除 fenced code，避免把示例输出识别为 Markdown 链接。"""

    kept: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.startswith("```"):
            inside = not inside
            continue
        if not inside:
            kept.append(line)
    return "\n".join(kept)


def public_markdown_files() -> list[Path]:
    roots = [ROOT / "README.md", ROOT / "README.zh.md", DOCS / "README.md"]
    roots.extend(sorted(EN_DIR.glob("*.md")))
    roots.extend(sorted(ZH_DIR.glob("*.md")))
    return roots


def markdown_files() -> list[Path]:
    files = public_markdown_files()
    files.extend(sorted((ROOT / ".agents" / "skills").glob("**/*.md")))
    return files


def check_pairs(contract: Contract) -> None:
    zh_names = {path.name for path in ZH_DIR.glob("*.md")}
    en_names = {path.name for path in EN_DIR.glob("*.md")}
    contract.require(zh_names == en_names, "docs/zh 与 docs/en 的 Markdown 文件名不一致")
    contract.require(
        REQUIRED_DOCS.issubset(zh_names),
        "双语文档缺少 architecture/getting-started/labs/reference 中的文件",
    )

    for name in sorted(zh_names & en_names):
        zh_path = ZH_DIR / name
        en_path = EN_DIR / name
        zh_lines = read_text(zh_path).splitlines()
        en_lines = read_text(en_path).splitlines()
        contract.require(bool(zh_lines), f"{zh_path.relative_to(ROOT)} 为空")
        contract.require(bool(en_lines), f"{en_path.relative_to(ROOT)} 为空")
        if not zh_lines or not en_lines:
            continue
        contract.require(
            zh_lines[0] == f"[English](../en/{name})",
            f"{zh_path.relative_to(ROOT)} 首行英文互链不符合合同",
        )
        contract.require(
            en_lines[0] == f"[中文](../zh/{name})",
            f"{en_path.relative_to(ROOT)} 首行中文互链不符合合同",
        )

        zh_levels = [len(item) for item in HEADING_RE.findall("\n".join(zh_lines))]
        en_levels = [len(item) for item in HEADING_RE.findall("\n".join(en_lines))]
        contract.require(
            zh_levels == en_levels,
            f"docs/zh/{name} 与 docs/en/{name} 的章节层级序列不一致",
        )


def normalized_link_target(raw_target: str) -> str:
    target = raw_target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    if " " in target:
        target = target.split(maxsplit=1)[0]
    return target


def check_local_links(contract: Contract) -> None:
    for source in markdown_files():
        text = without_fenced_blocks(read_text(source))
        for match in LINK_RE.finditer(text):
            raw_target = normalized_link_target(match.group(1))
            parsed = urlsplit(raw_target)
            if parsed.scheme or parsed.netloc:
                continue
            target_part = unquote(parsed.path)
            fragment = unquote(parsed.fragment)
            target = source if not target_part else (source.parent / target_part).resolve()
            try:
                target.relative_to(ROOT)
            except ValueError:
                contract.errors.append(
                    f"{source.relative_to(ROOT)} 的链接越出仓库：{raw_target}"
                )
                continue
            contract.require(
                target.exists(),
                f"{source.relative_to(ROOT)} 的本地链接目标不存在：{raw_target}",
            )
            if not fragment or not target.is_file():
                continue
            anchors = set(ANCHOR_RE.findall(read_text(target)))
            contract.require(
                fragment in anchors,
                f"{source.relative_to(ROOT)} 的锚点不存在或未显式声明：{raw_target}",
            )


def lab_block(text: str, index: int) -> str:
    start_marker = f'<a id="{LAB_ANCHORS[index]}"></a>'
    start = text.index(start_marker)
    if index + 1 == len(LAB_ANCHORS):
        return text[start:]
    end_marker = f'<a id="{LAB_ANCHORS[index + 1]}"></a>'
    return text[start:text.index(end_marker)]


def check_labs(contract: Contract) -> None:
    for path, sections in ((ZH_DIR / "labs.md", ZH_LAB_SECTIONS),
                           (EN_DIR / "labs.md", EN_LAB_SECTIONS)):
        text = read_text(path)
        relative = path.relative_to(ROOT)
        for anchor in NAVIGATION_ANCHORS:
            contract.require(
                text.count(f'<a id="{anchor}"></a>') == 1,
                f"{relative} 中锚点 {anchor} 必须且只能出现一次",
            )
        for index, anchor in enumerate(LAB_ANCHORS):
            try:
                block = lab_block(text, index)
            except ValueError:
                contract.errors.append(f"{relative} 无法按锚点 {anchor} 切分实验")
                continue
            for section in sections:
                contract.require(
                    f"### {section}" in block,
                    f"{relative} 的 {anchor} 缺少小节：{section}",
                )
            boundary_heading = sections[1]
            try:
                boundary = block.split(f"### {boundary_heading}", 1)[1]
                boundary = boundary.split("\n### ", 1)[0]
            except IndexError:
                boundary = ""
            for label in ("[REAL AMD]", "[GEM5]", "[COSIM]"):
                contract.require(
                    label in boundary,
                    f"{relative} 的 {anchor} 边界小节缺少层级标签：{label}",
                )

        try:
            lab2 = lab_block(text, 1)
        except ValueError:
            lab2 = ""
        for required in (
            "cleanup_lab02_probe()",
            "trap 'cleanup_lab02_probe $?' EXIT",
            './scripts/run_cosim_tests.sh --keep-alive',
            '--manifest "$MANIFEST" --confirm',
            "driver-rocm-probe.txt",
            "resources.manifest.snapshot",
            "lab02-cleanup-status.txt",
            'kill -TERM -- "-${launcher_pid}"',
        ):
            contract.require(required in lab2, f"{relative} 的 Lab 2 缺少安全探针合同：{required}")
        trap_position = lab2.find("trap 'cleanup_lab02_probe $?' EXIT")
        launch_position = lab2.find("./scripts/run_cosim_tests.sh --keep-alive")
        stop_position = lab2.find('kill -TERM -- "-${launcher_pid}"')
        fallback_position = lab2.find("./scripts/cosim_cleanup.sh --run-id")
        contract.require(
            0 <= trap_position < launch_position,
            f"{relative} 的 Lab 2 必须在 keep-alive 启动前安装 EXIT trap",
        )
        contract.require(
            0 <= stop_position < fallback_position,
            f"{relative} 的 Lab 2 必须先停止 launcher，再允许 manifest fallback",
        )
        contract.require(
            "```bash\n(\nset -euo pipefail" in text and "\nexit 0\n)\n```" in lab2,
            f"{relative} 的 Lab 2 诊断范式必须封装在 subshell",
        )
        contract.require(
            "amdgpu_doorbell.c" not in text and "amdgpu_doorbell_mgr.c" in text,
            f"{relative} 未使用固定 Driver 的 doorbell manager 源码路径",
        )

    for index_path in (ROOT / "README.md", ROOT / "README.zh.md", DOCS / "README.md"):
        text = read_text(index_path)
        for anchor in NAVIGATION_ANCHORS:
            contract.require(
                f"#{anchor}" in text,
                f"{index_path.relative_to(ROOT)} 缺少学习主题导航：{anchor}",
            )


def check_issue_playbooks(contract: Contract) -> None:
    language_contracts = (
        (
            ZH_DIR / "reference.md",
            "### 6.4 问题级 playbook",
            ("触发签名", "当前契约", "必须采集", "安全处置", "禁止操作", "完成条件"),
        ),
        (
            EN_DIR / "reference.md",
            "### 6.4 Issue-level playbooks",
            ("Trigger", "Current contract", "Capture", "Safe action", "Forbidden", "Done"),
        ),
    )
    for path, playbook_heading, fields in language_contracts:
        text = read_text(path)
        relative = path.relative_to(ROOT)
        contract.require(
            text.count('<a id="known-issue-playbook"></a>') == 1,
            f"{relative} 缺少唯一 known-issue-playbook 锚点",
        )
        for old_id, signature in enumerate(ISSUE_SIGNATURES, start=1):
            contract.require(
                f"| 4.{old_id} |" in text,
                f"{relative} 的历史故障签名索引缺少 4.{old_id}",
            )
            contract.require(
                signature in text,
                f"{relative} 的历史故障 4.{old_id} 缺少 canonical signature：{signature}",
            )
        try:
            playbooks = text.split(playbook_heading, 1)[1].split("\n## 7.", 1)[0]
        except IndexError:
            contract.errors.append(f"{relative} 无法切分问题级 playbook")
            continue
        blocks = [item for item in re.split(r"\n#### ", playbooks) if item.strip()]
        contract.require(len(blocks) == 5, f"{relative} 必须包含 5 个问题级 playbook")
        for index, block in enumerate(blocks, start=1):
            for field in fields:
                contract.require(
                    re.search(rf"^- {re.escape(field)}(?:：|:)", block, re.M) is not None,
                    f"{relative} 的 playbook {index} 缺少字段：{field}",
                )


def executable_fence_lines(path: Path) -> list[tuple[int, str]]:
    """返回 shell 类 fenced block 内可执行的行及其行号。"""

    results: list[tuple[int, str]] = []
    inside = False
    shell_fence = False
    for number, line in enumerate(read_text(path).splitlines(), start=1):
        fence = FENCE_RE.match(line)
        if fence:
            if inside:
                inside = False
                shell_fence = False
            else:
                language = fence.group(1).strip().lower()
                inside = True
                shell_fence = language in {"bash", "sh", "shell", "console"}
            continue
        if inside and shell_fence:
            stripped = line.strip()
            if stripped.startswith("$ "):
                stripped = stripped[2:].lstrip()
            if stripped and not stripped.startswith("#"):
                results.append((number, stripped))
    return results


def shell_fence_blocks_from_text(text: str) -> list[tuple[int, str]]:
    """返回给定 Markdown 中 bash/sh/shell fenced block 的起始行与内容。"""

    blocks: list[tuple[int, str]] = []
    inside = False
    shell_fence = False
    start = 0
    lines: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        fence = FENCE_RE.match(line)
        if fence:
            if inside:
                if shell_fence:
                    blocks.append((start, "\n".join(lines) + "\n"))
                inside = False
                shell_fence = False
                lines = []
            else:
                language = fence.group(1).strip().lower()
                inside = True
                shell_fence = language in {"bash", "sh", "shell"}
                start = number + 1
            continue
        if inside and shell_fence:
            lines.append(line)
    return blocks


def shell_fence_blocks(path: Path) -> list[tuple[int, str]]:
    """返回 Markdown 文件中 bash/sh/shell fenced block 的起始行与内容。"""

    return shell_fence_blocks_from_text(read_text(path))


def normalized_lab_shell(block: str) -> str:
    """忽略双语代码中只承担说明作用的注释和本地化错误文本。"""

    normalized: list[str] = []
    for line in block.splitlines():
        if line.lstrip().startswith("#"):
            continue
        line = re.sub(
            r'echo "Lab 2 [^"]*" >&2',
            'echo "<localized-message>" >&2',
            line,
        )
        normalized.append(line.rstrip())
    return "\n".join(normalized).strip()


def lab_source_section(block: str, heading: str) -> str:
    """返回 Markdown 三级标题对应的小节。"""

    try:
        return block.split(f"### {heading}", 1)[1].split("\n### ", 1)[0]
    except IndexError:
        return ""


def replace_in_markdown_section(
    text: str, heading: str, old: str, new: str
) -> str:
    """只替换指定三级标题小节中的首个匹配项。"""

    marker = f"### {heading}"
    marker_start = text.find(marker)
    if marker_start < 0:
        return text
    section_start = marker_start + len(marker)
    section_end = text.find("\n### ", section_start)
    if section_end < 0:
        section_end = len(text)
    section = text[section_start:section_end]
    if not section or old not in section:
        return text
    mutated = section.replace(old, new, 1)
    return text[:section_start] + mutated + text[section_end:]


def markdown_heading_section(text: str, heading: str) -> str:
    """返回完整 Markdown 标题下、下一个同级或更高层标题前的正文。"""

    marker_start = text.find(heading)
    if marker_start < 0:
        return ""
    level = len(heading) - len(heading.lstrip("#"))
    if level < 1 or not heading[level:].startswith(" "):
        return ""
    body_start = text.find("\n", marker_start)
    if body_start < 0:
        return ""
    body_start += 1
    next_heading = re.search(rf"^#{{1,{level}}}\s+", text[body_start:], re.M)
    if next_heading is None:
        return text[body_start:]
    return text[body_start:body_start + next_heading.start()]


def ordered_tokens_present(text: str, tokens: tuple[str, ...]) -> bool:
    """确认 token 按给定顺序出现。"""

    position = 0
    for token in tokens:
        found = text.find(token, position)
        if found < 0:
            return False
        position = found + len(token)
    return True


def confirmed_cleanup_block_issues(block: str) -> list[str]:
    """校验中断恢复命令的 launcher ownership、停止顺序与 exact manifest。"""

    issues: list[str] = []
    if "./scripts/cosim_cleanup.sh" not in block:
        issues.append("cleanup shell block 未使用仓库 wrapper")
        return issues
    if "--confirm" not in block:
        issues.append("cleanup shell block 缺少经过相同 scope 的 confirm")

    required_before_stop = (
        "launcher.pid",
        "ps -o pgid= -p",
        "ps -eo pgid=",
        "/proc/${",
        "scripts/cosim_launch.sh",
        "--artifact-dir ",
    )
    term_match = re.search(
        r'(?m)^\s*kill -TERM -- "-\$\{(?:LAUNCH_PID|launcher_pid)\}"',
        block,
    )
    if term_match is None:
        issues.append("未按 run-scoped launcher PID 停止精确 process group")
        term_position = -1
    else:
        term_position = term_match.start()
    for token in required_before_stop:
        position = block.find(token)
        if position < 0 or (term_position >= 0 and position > term_position):
            issues.append(f"停止 launcher 前缺少 ownership 校验：{token}")

    pgid_binding = (
        '"$LAUNCH_PGID" == "$LAUNCH_PID"' in block or
        '"$launcher_pgid" == "$launcher_pid"' in block
    )
    if not pgid_binding:
        issues.append("未证明 launcher PID 是其 process group leader")

    upper_probe = (
        'if launcher_group_alive; then\n'
        '    echo "launcher process group is still live; refusing cleanup" >&2'
    )
    upper_guard = '[[ "$GROUP_STATE" -eq 1 ]] || {'
    lower_probe = 'group_state=$?'
    lower_guard = 'if [[ "$launcher_stopped" -eq 1 ]]; then'
    if upper_probe in block and upper_guard in block:
        exit_gate_position = block.find(upper_guard)
    elif lower_probe in block and lower_guard in block:
        exit_gate_position = block.find(lower_guard)
    else:
        exit_gate_position = -1
        issues.append("exact launcher process group 退出确认未门控 fallback cleanup")

    confirm_position = block.rfind("--confirm")
    first_wait_position = block.find("for _ in {1..15}; do", term_position)
    kill_match = re.search(
        r'(?m)^\s*kill -KILL -- "-\$\{(?:LAUNCH_PID|launcher_pid)\}"',
        block[term_position:] if term_position >= 0 else "",
    )
    kill_position = (
        term_position + kill_match.start()
        if term_position >= 0 and kill_match is not None else -1
    )
    second_wait_position = block.find("for _ in {1..5}; do", kill_position)
    if not (
        0 <= term_position < first_wait_position < kill_position <
        second_wait_position < exit_gate_position < confirm_position
    ):
        issues.append(
            "必须按 TERM、有界等待、KILL fallback、再次等待、退出确认、"
            "exact-manifest confirm 的顺序恢复"
        )
    if re.search(r'--manifest\s+"\$MANIFEST"\s+--confirm', block) is None:
        issues.append("confirmed cleanup 未显式绑定 exact manifest")

    if BROAD_KILL_RE.search(block):
        issues.append("cleanup shell block 含宽泛 kill")
    for match in TERMINATING_KILL_RE.finditer(block):
        if re.search(r'--\s+"-\$\{[A-Za-z_][A-Za-z0-9_]*\}"', match.group(1)) is None:
            issues.append("终止命令没有限定到已验证的精确 process group")
    return issues


def phase6_recovery_contract_issues(documents: dict[str, str]) -> list[str]:
    """返回 Phase 6 架构边界与中断恢复文档合同的漂移。"""

    issues: list[str] = []
    expected_names = {str(path.relative_to(ROOT)) for path in PHASE6_DOC_PATHS}
    missing = expected_names.difference(documents)
    for name in sorted(missing):
        issues.append(f"缺少 Phase 6 文档：{name}")
    if missing:
        return issues

    en_arch = documents["docs/en/architecture.md"]
    zh_arch = documents["docs/zh/architecture.md"]
    en_arch_flat = " ".join(en_arch.split())
    zh_arch_flat = " ".join(zh_arch.split())
    if (
        "describes the implementation scope in this repository; measured "
        "boundaries are called out separately" not in en_arch_flat
    ):
        issues.append("docs/en/architecture.md 开篇未区分实现范围与实测边界")
    if "本文描述当前仓库的实现范围，实测边界另行标注" not in zh_arch_flat:
        issues.append("docs/zh/architecture.md 开篇未区分实现范围与实测边界")
    if "architecture that is implemented and measured" in en_arch_flat:
        issues.append("docs/en/architecture.md 仍把全部实现描述为已实测")
    if "只描述当前仓库已经实现且有实测依据的架构" in zh_arch_flat:
        issues.append("docs/zh/architecture.md 仍把全部实现描述为已实测")

    architecture_sections = (
        (
            "docs/en/architecture.md",
            markdown_heading_section(en_arch, "### 2.3 Session and disk isolation"),
            (
                "`launcher.pid`",
                "`scripts/cosim_launch.sh`",
                "stop that exact process group",
                "confirm that it has exited",
                "Only then",
                "exact manifest",
                "`cosim_cleanup.sh`",
            ),
        ),
        (
            "docs/zh/architecture.md",
            markdown_heading_section(zh_arch, "### 2.3 会话与磁盘隔离"),
            (
                "`launcher.pid`",
                "`scripts/cosim_launch.sh`",
                "停止该精确 process group",
                "确认它已经退出",
                "随后才允许",
                "exact manifest",
                "`cosim_cleanup.sh`",
            ),
        ),
    )
    for name, section, sequence in architecture_sections:
        flat = " ".join(section.split())
        if not section or not ordered_tokens_present(flat, sequence):
            issues.append(f"{name} 的 session isolation 缺少先停 launcher、后 cleanup 顺序")
        if "broad kill" not in section and "宽泛 kill" not in section:
            issues.append(f"{name} 的 session isolation 未禁止宽泛 kill")

    getting_sections: dict[str, str] = {}
    for name, heading in (
        ("docs/en/getting-started.md", "## Manifest-scoped cleanup"),
        ("docs/zh/getting-started.md", "## Manifest 范围内的清理"),
    ):
        content = documents[name]
        if '<a id="manifest-scoped-cleanup"></a>' not in content:
            issues.append(f"{name} 缺少 manifest-scoped-cleanup 显式锚点")
        section = markdown_heading_section(content, heading)
        getting_sections[name] = section
        for token in (
            "`launcher.pid`",
            "`scripts/cosim_launch.sh`",
            "`--artifact-dir`",
            "exact manifest",
            "runner-invocation.txt",
            'grep -Fxq "run_id=${RUN_ID}"',
            "/proc/${LAUNCH_PID}/environ",
            'grep -Fxq "COSIM_RUN_ID=${RUN_ID}"',
        ):
            if token not in section:
                issues.append(f"{name} 的 cleanup 小节缺少 token：{token}")
        cleanup_blocks = [
            block for _, block in shell_fence_blocks_from_text(section)
            if "./scripts/cosim_cleanup.sh" in block
        ]
        if len(cleanup_blocks) != 1:
            issues.append(f"{name} 必须包含唯一 canonical interrupted-cleanup shell block")
    en_cleanup_blocks = shell_fence_blocks_from_text(
        getting_sections["docs/en/getting-started.md"]
    )
    zh_cleanup_blocks = shell_fence_blocks_from_text(
        getting_sections["docs/zh/getting-started.md"]
    )
    if [block for _, block in en_cleanup_blocks] != [
        block for _, block in zh_cleanup_blocks
    ]:
        issues.append("双语 Getting Started 的 interrupted-cleanup 命令不等价")

    reference_sections = (
        (
            "docs/en/reference.md",
            markdown_heading_section(
                documents["docs/en/reference.md"],
                "### 2.4 Standalone learning launch and cleanup",
            ),
            "[run-scoped recovery procedure](getting-started.md#manifest-scoped-cleanup)",
            "stops and confirms exit of that group, and only then permits",
        ),
        (
            "docs/zh/reference.md",
            markdown_heading_section(
                documents["docs/zh/reference.md"],
                "### 2.4 Standalone 学习启动与清理",
            ),
            "[run-scoped recovery 流程](getting-started.md#manifest-scoped-cleanup)",
            "停止该 group 并确认退出，随后才允许",
        ),
    )
    for name, section, link, order_text in reference_sections:
        flat = " ".join(section.split())
        for token in (link, "`launcher.pid`", "`scripts/cosim_launch.sh`", "exact manifest"):
            if token not in flat:
                issues.append(f"{name} 的 standalone recovery 缺少 token：{token}")
        if order_text not in flat:
            issues.append(f"{name} 的 standalone recovery 未锁定先停后 cleanup")
        if "--confirm" in section:
            issues.append(f"{name} 的 standalone launch 不得给出 manifest-only confirm")

    for name, recovery_heading, sequence in (
        (
            "docs/en/labs.md",
            EN_LAB_SECTIONS[-1],
            (
                "`launcher.pid`",
                "`scripts/cosim_launch.sh`",
                "`--artifact-dir`",
                "stop that exact group and confirm its exit",
                "`cosim_cleanup.sh`",
                "exact manifest",
            ),
        ),
        (
            "docs/zh/labs.md",
            ZH_LAB_SECTIONS[-1],
            (
                "`launcher.pid`",
                "`scripts/cosim_launch.sh`",
                "`--artifact-dir`",
                "停止该精确 group 并确认退出",
                "`cosim_cleanup.sh`",
                "exact manifest",
            ),
        ),
    ):
        text = documents[name]
        for index, anchor in enumerate(LAB_ANCHORS):
            try:
                recovery = lab_source_section(lab_block(text, index), recovery_heading)
            except ValueError:
                recovery = ""
            if "getting-started.md#manifest-scoped-cleanup" not in recovery:
                issues.append(f"{name} 的 {anchor} recovery 未链接 canonical 流程")
            if not ordered_tokens_present(" ".join(recovery.split()), sequence):
                issues.append(f"{name} 的 {anchor} recovery 未锁定先停后 cleanup 顺序")
            if "broad kill" not in recovery and "宽泛 kill" not in recovery:
                issues.append(f"{name} 的 {anchor} recovery 未禁止宽泛 kill")

    cleanup_block_count = 0
    for name, text in documents.items():
        for start, block in shell_fence_blocks_from_text(text):
            if BROAD_KILL_RE.search(block):
                issues.append(f"{name}:{start} 含宽泛 kill 命令")
            for match in TERMINATING_KILL_RE.finditer(block):
                if re.search(
                    r'--\s+"-\$\{[A-Za-z_][A-Za-z0-9_]*\}"', match.group(1)
                ) is None:
                    issues.append(f"{name}:{start} 的终止命令未限定精确 process group")
            if "./scripts/cosim_cleanup.sh" not in block:
                continue
            cleanup_block_count += 1
            for issue in confirmed_cleanup_block_issues(block):
                issues.append(f"{name}:{start} {issue}")
    if cleanup_block_count < 4:
        issues.append("双语文档缺少 Getting Started 与 Lab 2 的安全 cleanup 命令")
    return issues


def check_phase6_recovery_mutation_guard(contract: Contract) -> None:
    """纯内存破坏恢复顺序、ownership 和架构边界，确认合同拒绝回归。"""

    documents = {
        str(path.relative_to(ROOT)): read_text(path) for path in PHASE6_DOC_PATHS
    }

    def mutate(name: str, old: str, new: str, count: int = 1) -> dict[str, str]:
        changed = documents[name].replace(old, new, count)
        contract.require(
            changed != documents[name],
            f"Phase 6 recovery mutation fixture 缺失：{name}: {old}",
        )
        return {**documents, name: changed}

    mutations = (
        mutate(
            "docs/en/architecture.md",
            "implementation scope in this repository; measured",
            "architecture that is implemented and measured; measured",
        ),
        mutate(
            "docs/zh/architecture.md",
            "实现范围，实测边界另行标注",
            "已经实现且有实测依据的架构",
        ),
        mutate(
            "docs/en/getting-started.md",
            'kill -TERM -- "-${LAUNCH_PID}"',
            'kill -TERM -- "${LAUNCH_PID}"',
        ),
        mutate(
            "docs/en/getting-started.md",
            '[[ "$GROUP_STATE" -eq 1 ]] || {',
            "true || {",
        ),
        mutate(
            "docs/en/getting-started.md",
            '--manifest "$MANIFEST" --confirm',
            "--confirm",
        ),
        mutate(
            "docs/en/getting-started.md",
            "for _ in {1..15}; do",
            "for _ in {1..0}; do",
        ),
        mutate(
            "docs/en/getting-started.md",
            'grep -Fxq "COSIM_RUN_ID=${RUN_ID}"',
            'grep -Fxq "COSIM_RUN_ID=wrong-run"',
        ),
        mutate(
            "docs/en/getting-started.md",
            '    kill -TERM -- "-${LAUNCH_PID}"',
            '    killall cosim_launch\n    kill -TERM -- "-${LAUNCH_PID}"',
        ),
        mutate(
            "docs/en/reference.md",
            "getting-started.md#manifest-scoped-cleanup",
            "getting-started.md",
        ),
        mutate(
            "docs/zh/labs.md",
            "停止该精确 group 并确认退出；随后才允许",
            "直接清理；随后再停止该 group；",
        ),
    )
    for index, mutated in enumerate(mutations, start=1):
        contract.require(
            bool(phase6_recovery_contract_issues(mutated)),
            f"Phase 6 recovery mutation {index} 未被拒绝",
        )


def check_phase6_recovery_contract(contract: Contract) -> None:
    """检查 8 个 Phase 6 双语文档的边界声明与安全 cleanup 流程。"""

    documents = {
        str(path.relative_to(ROOT)): read_text(path) for path in PHASE6_DOC_PATHS
    }
    contract.errors.extend(phase6_recovery_contract_issues(documents))
    check_phase6_recovery_mutation_guard(contract)


def modeled_source_chunks(section: str) -> str:
    """只保留 GEM5/COSIM 源码 bullet，排除外部 Driver/ROCm 函数。"""

    kept: list[str] = []
    active = False
    for line in section.splitlines():
        if line.startswith("- `[GEM5]`") or line.startswith("- `[COSIM]`"):
            active = True
        elif line.startswith("- `["):
            active = False
        if active:
            kept.append(line)
    return "\n".join(kept)


def modeled_local_source_paths(section: str) -> list[tuple[str, str | None]]:
    """解析 GEM5/COSIM bullet 中的完整路径及相对源码文件名。"""

    paths: list[tuple[str, str | None]] = []
    active = False
    base_dir: Path | None = None
    for line in section.splitlines():
        if line.startswith("- `[GEM5]`") or line.startswith("- `[COSIM]`"):
            active = True
            base_dir = None
        elif line.startswith("- `["):
            active = False
            base_dir = None
        if not active:
            continue
        for token in INLINE_CODE_RE.findall(line):
            if LOCAL_SOURCE_PATH_RE.fullmatch(token):
                paths.append((token, token))
                base_dir = Path(token).parent
            elif RELATIVE_SOURCE_PATH_RE.fullmatch(token):
                resolved = None
                if base_dir is not None:
                    resolved = (base_dir / token).as_posix()
                paths.append((token, resolved))
    return paths


def repository_source_path_exists(raw_path: str) -> bool:
    """确认解析后的源码路径位于仓库内且真实存在。"""

    if any(char in raw_path for char in "*{}"):
        return False
    target = (ROOT / raw_path).resolve()
    try:
        target.relative_to(ROOT)
    except ValueError:
        return False
    return target.exists()


def modeled_source_path_issues(section: str) -> list[str]:
    """返回 GEM5/COSIM 源码导航小节中的路径问题。"""

    issues: list[str] = []
    for token, resolved in modeled_local_source_paths(section):
        if resolved is None:
            issues.append(f"相对源码路径缺少可解析的基准目录：{token}")
        elif not repository_source_path_exists(resolved):
            issues.append(f"本地源码路径无法定位：{token} -> {resolved}")
    return issues


def modeled_source_identifier_issues(section: str) -> list[str]:
    """确认 GEM5/COSIM identifier 确实位于最近声明的本地源码文件。"""

    issues: list[str] = []
    active = False
    base_dir: Path | None = None
    source_path: str | None = None
    source_text = ""
    for line in section.splitlines():
        if line.startswith("- `[GEM5]`") or line.startswith("- `[COSIM]`"):
            active = True
            base_dir = None
            source_path = None
            source_text = ""
        elif line.startswith("- `["):
            active = False
            base_dir = None
            source_path = None
            source_text = ""
        if not active:
            continue
        for token in INLINE_CODE_RE.findall(line):
            resolved: str | None = None
            if LOCAL_SOURCE_PATH_RE.fullmatch(token):
                resolved = token
                base_dir = Path(token).parent
            elif RELATIVE_SOURCE_PATH_RE.fullmatch(token):
                if base_dir is not None:
                    resolved = (base_dir / token).as_posix()
            if resolved is not None:
                source_path = resolved
                target = ROOT / resolved
                source_text = read_text(target) if target.is_file() else ""
                continue
            if not IDENTIFIER_TOKEN_RE.fullmatch(token):
                continue
            identifier = token.rstrip("*")
            if source_path is None:
                issues.append(f"源码标识缺少关联的本地源码路径：{identifier}")
                continue
            if identifier not in source_text:
                issues.append(f"源码标识不属于所列文件：{identifier} -> {source_path}")
    return issues


def check_relative_source_path_mutation_guard(contract: Contract) -> None:
    """纯内存变异一个相对源码文件名，确认路径合同会拒绝它。"""

    language_cases = (
        (ZH_DIR / "labs.md", ZH_LAB_SECTIONS[3], "zh"),
        (EN_DIR / "labs.md", EN_LAB_SECTIONS[3], "en"),
    )
    for path, heading, language in language_cases:
        text = read_text(path)
        selected: tuple[str, str] | None = None
        for index in range(len(LAB_ANCHORS)):
            try:
                section = lab_source_section(lab_block(text, index), heading)
            except ValueError:
                continue
            relative_paths = [
                token for token, resolved in modeled_local_source_paths(section)
                if resolved is not None and token != resolved
            ]
            if relative_paths:
                selected = (section, relative_paths[0])
                break
        contract.require(
            selected is not None,
            f"{path.relative_to(ROOT)} 缺少相对源码路径 mutation fixture",
        )
        if selected is None:
            continue
        section, token = selected
        missing = f"__docs_contract_missing_source_{language}__.cc"
        mutated = section.replace(f"`{token}`", f"`{missing}`", 1)
        issues = modeled_source_path_issues(mutated)
        contract.require(
            any(missing in issue for issue in issues),
            f"{path.relative_to(ROOT)} 的相对源码路径 mutation 未被拒绝",
        )


def check_source_identifier_mutation_guard(contract: Contract) -> None:
    """破坏路径绑定，确认函数到文件的关联合同会失败。"""

    for path, heading in (
        (ZH_DIR / "labs.md", ZH_LAB_SECTIONS[3]),
        (EN_DIR / "labs.md", EN_LAB_SECTIONS[3]),
    ):
        section = lab_source_section(lab_block(read_text(path), 2), heading)
        first = "gem5/src/dev/amdgpu/amdgpu_vm.cc"
        second = "amdgpu_device.cc"
        placeholder = "__docs_contract_source_swap__.cc"
        mutated = section.replace(first, placeholder, 1)
        mutated = mutated.replace(second, "amdgpu_vm.cc", 1)
        mutated = mutated.replace(placeholder, "gem5/src/dev/amdgpu/amdgpu_device.cc", 1)
        contract.require(
            bool(modeled_source_identifier_issues(mutated)),
            f"{path.relative_to(ROOT)} 的源码文件/函数 swap mutation 未被拒绝",
        )

        first_identifier = "AMDGPUVM::writeMMIOGfx940"
        second_identifier = "AMDGPUDevice::writeFrame"
        identifier_placeholder = "__docs_contract_identifier_swap__"
        identifier_mutated = section.replace(
            first_identifier, identifier_placeholder, 1
        )
        identifier_mutated = identifier_mutated.replace(
            second_identifier, first_identifier, 1
        )
        identifier_mutated = identifier_mutated.replace(
            identifier_placeholder, second_identifier, 1
        )
        contract.require(
            bool(modeled_source_identifier_issues(identifier_mutated)),
            f"{path.relative_to(ROOT)} 的源码函数 swap mutation 未被拒绝",
        )

        lab4_section = lab_source_section(lab_block(read_text(path), 3), heading)
        source_path = "gem5/src/dev/amdgpu/mi300x_vfio_user.cc"
        pathless = lab4_section.replace(f"`{source_path}`", "", 1)
        pathless_issues = modeled_source_identifier_issues(pathless)
        contract.require(
            any(
                "MI300XVfioUser::handleDoorbellAccess" in issue
                and "缺少关联的本地源码路径" in issue
                for issue in pathless_issues
            ),
            f"{path.relative_to(ROOT)} 的无路径源码标识 mutation 未被拒绝",
        )


def evidence_contract_issues(
    documents: dict[str, str], runner: str, launcher: str, verifier: str
) -> list[str]:
    """返回双语证据文档与 producer/verifier schema 的漂移。"""

    issues: list[str] = []
    for name, content in documents.items():
        for token in EVIDENCE_CONTRACT_TOKENS:
            if token not in content:
                issues.append(f"{name} 缺少严格证据 token：{token}")
    acceptance_sections = (
        ("docs/zh/labs.md", "标准验收 artifact"),
        ("docs/en/labs.md", "Standard acceptance artifacts"),
    )
    for name, heading in acceptance_sections:
        section = lab_source_section(documents.get(name, ""), heading)
        if not section:
            issues.append(f"{name} 缺少 {heading} 小节")
            continue
        for token in EVIDENCE_CONTRACT_TOKENS:
            if token not in section:
                issues.append(f"{name} 的 {heading} 小节缺少严格证据 token：{token}")
    producer_contracts = (
        (runner, 'echo "schema=cosim-runner-invocation/v1"', "runner schema"),
        (
            runner,
            '${RUNNER_ARTIFACT_DIR}/runner-invocation.txt',
            "runner invocation writer",
        ),
        (launcher, 'echo "schema=cosim-launch-invocation/v1"', "launcher schema"),
        (
            launcher,
            '${ARTIFACT_DIR}/launch-invocation.txt',
            "launcher invocation writer",
        ),
        (verifier, 'SCHEMA = "cosim-matrix-verification/v2"', "matrix schema"),
        (verifier, '"cosim-runner-invocation/v1"', "runner verifier schema"),
        (verifier, '"cosim-launch-invocation/v1"', "launcher verifier schema"),
    )
    for content, token, role in producer_contracts:
        if token not in content:
            issues.append(f"producer/verifier 缺少 {role}：{token}")
    return issues


def check_evidence_contract_mutation_guard(contract: Contract) -> None:
    """纯内存破坏文档和 producer schema，确认合同会拒绝漂移。"""

    documents = {
        str(path.relative_to(ROOT)): read_text(path)
        for path in (
            ZH_DIR / "getting-started.md",
            EN_DIR / "getting-started.md",
            ZH_DIR / "labs.md",
            EN_DIR / "labs.md",
            ZH_DIR / "reference.md",
            EN_DIR / "reference.md",
        )
    }
    runner = read_text(ROOT / "scripts" / "run_cosim_tests.sh")
    launcher = read_text(ROOT / "scripts" / "cosim_launch.sh")
    verifier = read_text(ROOT / "scripts" / "verify_cosim_matrix.py")
    acceptance_mutations = tuple(
        (
            {
                **documents,
                name: replace_in_markdown_section(
                    documents[name],
                    heading,
                    "runner-invocation.txt",
                    "runner-command.txt",
                ),
            },
            runner,
            launcher,
            verifier,
        )
        for name, heading in (
            ("docs/zh/labs.md", "标准验收 artifact"),
            ("docs/en/labs.md", "Standard acceptance artifacts"),
        )
    )
    mutations = (
        *acceptance_mutations,
        (
            {name: content.replace("cosim-matrix-verification/v2", "cosim-matrix-verification/v1")
             for name, content in documents.items()},
            runner,
            launcher,
            verifier,
        ),
        (documents, runner.replace("cosim-runner-invocation/v1", "cosim-runner-invocation/v0"), launcher, verifier),
        (documents, runner, launcher.replace("launch-invocation.txt", "launch-command.txt"), verifier),
    )
    for index, mutation in enumerate(mutations, start=1):
        contract.require(
            bool(evidence_contract_issues(*mutation)),
            f"证据合同 mutation {index} 未被拒绝",
        )


def logical_shell_commands(block: str) -> list[str]:
    """合并反斜杠续行，用于校验 wrapper option 的命令归属。"""

    commands: list[str] = []
    current = ""
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.endswith("\\"):
            current += stripped[:-1].rstrip() + " "
            continue
        current += stripped
        commands.append(current)
        current = ""
    if current:
        commands.append(current)
    return commands


def runner_commands(text: str) -> list[str]:
    """返回 Markdown shell block 中调用 fresh-session runner 的逻辑命令。"""

    commands: list[str] = []
    for _, block in shell_fence_blocks_from_text(text):
        commands.extend(
            command for command in logical_shell_commands(block)
            if "./scripts/run_cosim_tests.sh" in command
        )
    return commands


def strict_acceptance_contract_issues(
    documents: dict[str, str], runner: str, verifier: str
) -> list[str]:
    """返回 strict v2 opt-in 与默认 diagnostic/dirty replay 边界漂移。"""

    issues: list[str] = []
    expected_names = {
        f"docs/{language}/{name}"
        for language in ("zh", "en")
        for name in ("getting-started.md", "labs.md", "reference.md")
    }
    for name in sorted(expected_names.difference(documents)):
        issues.append(f"strict acceptance 合同缺少文档：{name}")

    for name, content in documents.items():
        flat = " ".join(content.split())
        for token in (
            "`COSIM_STRICT_ACCEPTANCE=1`",
            "`cosim-matrix-verification/v2`",
            "diagnostic mode",
            "dirty replay",
            "clean HEAD",
        ):
            if token not in flat:
                issues.append(f"{name} 缺少 strict/diagnostic 边界 token：{token}")
        if name.startswith("docs/en/"):
            if "Only artifacts" not in flat or "final v2 matrix" not in flat:
                issues.append(f"{name} 未声明只有 strict artifact 可进入 final v2 matrix")
        elif "只有记录" not in flat or "final v2 matrix" not in flat:
            issues.append(f"{name} 未声明只有 strict artifact 可进入 final v2 matrix")

    getting_contracts = (
        (
            "docs/en/getting-started.md",
            "## Run fresh-session HIP tests",
        ),
        (
            "docs/zh/getting-started.md",
            "## 运行全新会话 HIP 测试",
        ),
    )
    for name, heading in getting_contracts:
        commands = runner_commands(
            markdown_heading_section(documents.get(name, ""), heading)
        )
        if len(commands) < 5:
            issues.append(f"{name} 缺少 diagnostic smoke 与 strict comparison/regression 命令")
            continue
        if "COSIM_STRICT_ACCEPTANCE=1" in commands[0]:
            issues.append(f"{name} 的普通学习 smoke 不得强制 strict/clean HEAD")
        for index, command in enumerate(commands[1:], start=2):
            if "COSIM_STRICT_ACCEPTANCE=1" not in command:
                issues.append(f"{name} 的 strict runner 命令 {index} 未显式 opt in")

    for name, accepted_heading, regression_heading, debug_heading in (
        (
            "docs/en/reference.md",
            "### 2.2 One accepted HIP run",
            "### 2.3 Regression and repetition",
            "## 7. Debug flag map",
        ),
        (
            "docs/zh/reference.md",
            "### 2.2 单条可验收 HIP 运行",
            "### 2.3 Regression 与重复运行",
            "## 7. Debug flag 导航",
        ),
    ):
        strict_commands = runner_commands(
            markdown_heading_section(documents.get(name, ""), accepted_heading)
        ) + runner_commands(
            markdown_heading_section(documents.get(name, ""), regression_heading)
        )
        if len(strict_commands) != 4:
            issues.append(f"{name} 必须保留 4 条 accepted/regression runner 命令")
        for index, command in enumerate(strict_commands, start=1):
            if "COSIM_STRICT_ACCEPTANCE=1" not in command:
                issues.append(f"{name} 的 accepted/regression 命令 {index} 未显式 strict")
        debug_commands = runner_commands(
            markdown_heading_section(documents.get(name, ""), debug_heading)
        )
        if len(debug_commands) != 1:
            issues.append(f"{name} 必须保留 1 条默认 diagnostic debug 命令")
        for command in debug_commands:
            if "COSIM_STRICT_ACCEPTANCE=1" in command:
                issues.append(f"{name} 的 dirty replay debug 命令不得强制 strict")

    for name in ("docs/en/labs.md", "docs/zh/labs.md"):
        commands = runner_commands(documents.get(name, ""))
        strict_count = 0
        diagnostic_count = 0
        for command in commands:
            if "--keep-alive" in command:
                diagnostic_count += 1
                if "COSIM_STRICT_ACCEPTANCE=1" in command:
                    issues.append(f"{name} 的 keep-alive probe 不得进入 strict v2")
            else:
                strict_count += 1
                if "COSIM_STRICT_ACCEPTANCE=1" not in command:
                    issues.append(f"{name} 的 accepted Lab 命令未显式 strict：{command}")
        if strict_count < len(LAB_ANCHORS) or diagnostic_count != 1:
            issues.append(f"{name} 的 strict Lab/diagnostic probe 命令分类不完整")

    producer_tokens = (
        'STRICT_ACCEPTANCE="${COSIM_STRICT_ACCEPTANCE:-0}"',
        '[[ "$STRICT_ACCEPTANCE" == "0" || "$STRICT_ACCEPTANCE" == "1" ]]',
        'if [[ "$STRICT_ACCEPTANCE" == "1" ]]; then',
        'echo "strict_acceptance=${STRICT_ACCEPTANCE}"',
        "strict_acceptance\\n'",
    )
    for token in producer_tokens:
        if token not in runner:
            issues.append(f"runner 缺少 strict acceptance producer token：{token}")
    clean_checks = [
        runner.find("top-level source tree must be clean before a strict acceptance run"),
        runner.find("gem5 source tree must be clean before a strict acceptance run"),
    ]
    first_clean_check = min(clean_checks)
    strict_gate = runner.rfind(
        'if [[ "$STRICT_ACCEPTANCE" == "1" ]]; then', 0, first_clean_check
    )
    gate_end = runner.find("\nfi", strict_gate)
    if strict_gate < 0 or gate_end < 0 or not all(
        strict_gate < position < gate_end for position in clean_checks
    ):
        issues.append("runner 的 clean-tree gate 未限定在 COSIM_STRICT_ACCEPTANCE=1")

    for token in (
        'manifest.get("strict_acceptance") != "1"',
        '"strict_acceptance_required"',
        'SCHEMA = "cosim-matrix-verification/v2"',
    ):
        if token not in verifier:
            issues.append(f"final v2 verifier 缺少 strict-only gate：{token}")
    return issues


def check_strict_acceptance_mutation_guard(contract: Contract) -> None:
    """纯内存破坏 strict/diagnostic 边界，确认文档合同拒绝回归。"""

    documents = {
        str(path.relative_to(ROOT)): read_text(path)
        for path in (
            ZH_DIR / "getting-started.md",
            EN_DIR / "getting-started.md",
            ZH_DIR / "labs.md",
            EN_DIR / "labs.md",
            ZH_DIR / "reference.md",
            EN_DIR / "reference.md",
        )
    }
    runner = read_text(ROOT / "scripts" / "run_cosim_tests.sh")
    verifier = read_text(ROOT / "scripts" / "verify_cosim_matrix.py")

    def mutate_document(name: str, old: str, new: str) -> dict[str, str]:
        changed = documents[name].replace(old, new, 1)
        contract.require(
            changed != documents[name],
            f"strict acceptance mutation fixture 缺失：{name}: {old}",
        )
        return {**documents, name: changed}

    mutations = (
        (
            mutate_document(
                "docs/en/getting-started.md",
                "COSIM_STRICT_ACCEPTANCE=1 GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=0",
                "GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=0",
            ),
            runner,
            verifier,
        ),
        (
            mutate_document(
                "docs/zh/labs.md",
                'COSIM_STRICT_ACCEPTANCE=1 COSIM_RUN_ID="$LAB_RUN_ID"',
                'COSIM_STRICT_ACCEPTANCE=0 COSIM_RUN_ID="$LAB_RUN_ID"',
            ),
            runner,
            verifier,
        ),
        (
            mutate_document(
                "docs/en/labs.md",
                'COSIM_RUN_ID="$PROBE_RUN_ID" GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=0',
                'COSIM_STRICT_ACCEPTANCE=1 COSIM_RUN_ID="$PROBE_RUN_ID" '
                "GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=0",
            ),
            runner,
            verifier,
        ),
        (
            mutate_document(
                "docs/zh/reference.md",
                'COSIM_RUN_ID="$RUN_ID" GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=0 \\\n'
                "    ./scripts/run_cosim_tests.sh \\",
                'COSIM_STRICT_ACCEPTANCE=1 COSIM_RUN_ID="$RUN_ID" '
                "GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=0 \\\n"
                "    ./scripts/run_cosim_tests.sh \\",
            ),
            runner,
            verifier,
        ),
        (
            documents,
            runner.replace(
                'STRICT_ACCEPTANCE="${COSIM_STRICT_ACCEPTANCE:-0}"',
                'STRICT_ACCEPTANCE="${COSIM_STRICT_ACCEPTANCE:-1}"',
                1,
            ),
            verifier,
        ),
        (
            documents,
            runner,
            verifier.replace(
                'manifest.get("strict_acceptance") != "1"',
                'manifest.get("strict_acceptance") != "0"',
                1,
            ),
        ),
    )
    for index, mutation in enumerate(mutations, start=1):
        contract.require(
            bool(strict_acceptance_contract_issues(*mutation)),
            f"strict acceptance mutation {index} 未被拒绝",
        )


def check_strict_acceptance_contract(contract: Contract) -> None:
    """检查双语 strict v2 命令与默认 diagnostic/dirty replay 合同。"""

    documents = {
        str(path.relative_to(ROOT)): read_text(path)
        for path in (
            ZH_DIR / "getting-started.md",
            EN_DIR / "getting-started.md",
            ZH_DIR / "labs.md",
            EN_DIR / "labs.md",
            ZH_DIR / "reference.md",
            EN_DIR / "reference.md",
        )
    }
    contract.errors.extend(
        strict_acceptance_contract_issues(
            documents,
            read_text(ROOT / "scripts" / "run_cosim_tests.sh"),
            read_text(ROOT / "scripts" / "verify_cosim_matrix.py"),
        )
    )
    check_strict_acceptance_mutation_guard(contract)


def check_shell_and_source_contracts(contract: Contract) -> None:
    policy_files = public_markdown_files() + [
        ROOT / "AGENTS.md",
        ROOT / "agents.md",
        *SKILL_MARKDOWN_FILES,
    ]
    for path in policy_files:
        for start, block in shell_fence_blocks(path):
            contract.require(
                re.search(r"(?m)^[A-Za-z_][A-Za-z0-9_]*=<[^>\n]+>\s*$", block)
                is None,
                f"{path.relative_to(ROOT)}:{start} 含不可执行的未引用 assignment 占位符",
            )
            parse_block = re.sub(r"<[-A-Za-z0-9_./]+>", "placeholder", block)
            result = subprocess.run(
                ["bash", "-n"],
                input=parse_block,
                check=False,
                capture_output=True,
                text=True,
            )
            contract.require(
                result.returncode == 0,
                f"{path.relative_to(ROOT)}:{start} 的 shell 区块语法错误："
                f"{result.stderr.strip()}",
            )

    zh_shell = shell_fence_blocks(ZH_DIR / "labs.md")
    en_shell = shell_fence_blocks(EN_DIR / "labs.md")
    contract.require(len(zh_shell) == len(en_shell), "双语 Labs 的 shell 区块数量不一致")
    for index, ((_, zh_block), (_, en_block)) in enumerate(
            zip(zh_shell, en_shell), start=1):
        contract.require(
            normalized_lab_shell(zh_block) == normalized_lab_shell(en_block),
            f"双语 Labs 的 shell 区块 {index} 存在非本地化语义差异",
        )

    for index, anchor in enumerate(LAB_ANCHORS):
        try:
            zh_block = lab_block(read_text(ZH_DIR / "labs.md"), index)
            en_block = lab_block(read_text(EN_DIR / "labs.md"), index)
        except ValueError:
            continue
        contract.require(
            set(INLINE_CODE_RE.findall(zh_block)) ==
            set(INLINE_CODE_RE.findall(en_block)),
            f"{anchor} 的双语技术标识不一致",
        )

    lab_text = read_text(ZH_DIR / "labs.md") + "\n" + read_text(EN_DIR / "labs.md")
    help_options: dict[str, set[str]] = {}
    for script in (
        "scripts/cosim_launch.sh",
        "scripts/run_cosim_tests.sh",
        "scripts/cosim_cleanup.sh",
        "scripts/cosim_preflight.sh",
    ):
        result = subprocess.run(
            ["bash", script, "--help"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        contract.require(result.returncode == 0, f"{script} --help 返回非零")
        help_options[script] = set(OPTION_RE.findall(result.stdout + result.stderr))
    # Runner 明确把未知 option 透传给 launcher，因此这两组 option 都属于 runner。
    help_options["scripts/run_cosim_tests.sh"].update(
        help_options["scripts/cosim_launch.sh"]
    )
    help_options["scripts/run_cosim_tests.sh"].difference_update(
        {"--share-dir", "--artifact-dir"}
    )
    for path in (ZH_DIR / "labs.md", EN_DIR / "labs.md"):
        for _, block in shell_fence_blocks(path):
            for command in logical_shell_commands(block):
                for script, allowed in help_options.items():
                    if script not in command and f"./{script}" not in command:
                        continue
                    for option in OPTION_RE.findall(command):
                        contract.require(
                            option in allowed,
                            f"{path.relative_to(ROOT)} 的 {script} 使用未知 option：{option}",
                        )

    debug_flags: set[str] = set()
    for path in (ROOT / "gem5" / "src").glob("**/SConscript"):
        debug_flags.update(re.findall(r"DebugFlag\(['\"]([^'\"]+)", read_text(path)))
    for group in DEBUG_OPTION_RE.findall(lab_text):
        for flag in group.split(","):
            contract.require(flag in debug_flags, f"Labs 使用未知 gem5 debug flag：{flag}")

    local_source = "\n".join(
        read_text(path)
        for tree in (
            ROOT / "gem5" / "src" / "dev" / "amdgpu",
            ROOT / "gem5" / "src" / "dev" / "hsa",
            ROOT / "gem5" / "src" / "gpu-compute",
        )
        for suffix in ("*.cc", "*.hh")
        for path in tree.glob(suffix)
    )
    modeled_identifiers: set[str] = set()
    for index in range(len(LAB_ANCHORS)):
        try:
            block = lab_block(read_text(ZH_DIR / "labs.md"), index)
        except ValueError:
            continue
        source = lab_source_section(block, ZH_LAB_SECTIONS[3])
        modeled_identifiers.update(
            CODE_IDENTIFIER_RE.findall(modeled_source_chunks(source))
        )
    for function in sorted(modeled_identifiers):
        contract.require(
            function.rstrip("*") in local_source,
            f"Labs 的本地 gem5 函数无法在锁定源码定位：{function}",
        )

    for path, heading in (
        (ZH_DIR / "labs.md", ZH_LAB_SECTIONS[3]),
        (EN_DIR / "labs.md", EN_LAB_SECTIONS[3]),
    ):
        text = read_text(path)
        for index, anchor in enumerate(LAB_ANCHORS):
            try:
                source = lab_source_section(lab_block(text, index), heading)
            except ValueError:
                continue
            for issue in modeled_source_path_issues(source):
                contract.errors.append(
                    f"{path.relative_to(ROOT)} 的 {anchor} {issue}"
                )
            for issue in modeled_source_identifier_issues(source):
                contract.errors.append(
                    f"{path.relative_to(ROOT)} 的 {anchor} {issue}"
                )

    for raw_path in sorted(set(SOURCE_PATH_RE.findall(lab_text))):
        contract.require(
            repository_source_path_exists(raw_path),
            f"Labs 的本地源码路径无法定位：{raw_path}",
        )

    check_relative_source_path_mutation_guard(contract)
    check_source_identifier_mutation_guard(contract)


def live_wait_contract_issues(text: str) -> list[str]:
    """返回 live-wait Guest/Host 双重 deadline 与采样门禁漂移。"""

    issues: list[str] = []
    required_tokens = (
        (
            'send_console "timeout --kill-after=5s 45s bash -c',
            "缺少 Guest-side timeout --kill-after=5s 45s",
        ),
        ("sample_rc=\\$?", "缺少 Guest wrapper 退出码采集"),
        (
            "echo __WAIT_SAMPLE_END__:${sample}:\\${sample_rc}",
            "END marker 未携带 Guest wrapper 退出码",
        ),
        (
            'grep -q "__WAIT_SAMPLE_END__:${sample}:0"',
            "未要求 Guest wrapper END marker 的退出码为 0",
        ),
    )
    for token, message in required_tokens:
        if token not in text:
            issues.append(message)

    sample_gate = re.search(
        r"if ! capture_wait_sample 1; then\n"
        r"(?:(?:    ).*\n)*?"
        r"    exit 1\n"
        r"fi\n"
        r"sleep 15\n"
        r"if ! capture_wait_sample 2; then",
        text,
    )
    if sample_gate is None:
        issues.append("sample 1 失败后未在 sample 2 前退出")
    return issues


def check_live_wait_mutation_guard(contract: Contract, text: str) -> None:
    """纯内存破坏 live-wait 门禁，确认合同拒绝 Host-only deadline 回归。"""

    mutations = (
        (
            "timeout --kill-after=5s 45s",
            "timeout 45s",
            "Guest-side timeout",
        ),
        (
            "echo __WAIT_SAMPLE_END__:${sample}:\\${sample_rc}",
            "echo __WAIT_SAMPLE_END__:${sample}",
            "带 rc 的 END marker",
        ),
        (
            "    exit 1\nfi\nsleep 15\nif ! capture_wait_sample 2; then",
            "    true\nfi\nsleep 15\nif ! capture_wait_sample 2; then",
            "sample 1 失败停止 sample 2",
        ),
    )
    for old, new, label in mutations:
        mutated = text.replace(old, new, 1)
        contract.require(mutated != text, f"live-wait {label} mutation fixture 缺失")
        contract.require(
            bool(live_wait_contract_issues(mutated)),
            f"live-wait {label} mutation 未被拒绝",
        )


def readme_cleanup_contract_issues(documents: dict[str, str]) -> list[str]:
    """返回根 README 的 dry-run inventory 与 ownership-gated recovery 漂移。"""

    issues: list[str] = []
    contracts = (
        (
            "README.md",
            "Run-scoped cleanup inventory (dry-run)",
            "Ownership-gated interrupted recovery",
            "docs/en/getting-started.md#manifest-scoped-cleanup",
        ),
        (
            "README.zh.md",
            "查看运行范围内的清理清单（dry-run）",
            "有 ownership gate 的中断恢复",
            "docs/zh/getting-started.md#manifest-scoped-cleanup",
        ),
    )
    for name, dry_label, recovery_label, recovery_link in contracts:
        text = documents.get(name, "")
        for token in (
            dry_label,
            recovery_label,
            recovery_link,
            "./scripts/cosim_cleanup.sh --run-id <id>",
            "`launcher.pid`",
            "process-group exit gate",
        ):
            if token not in text:
                issues.append(f"{name} 缺少 README cleanup token：{token}")
        if "--confirm" in text:
            issues.append(f"{name} 不得给出绕过 ownership gate 的裸 --confirm")
    return issues


def check_readme_cleanup_mutation_guard(contract: Contract) -> None:
    """纯内存恢复 README 裸 confirm，确认合同拒绝回归。"""

    documents = {
        path.name: read_text(path)
        for path in (ROOT / "README.md", ROOT / "README.zh.md")
    }
    mutations = (
        {
            **documents,
            "README.md": documents["README.md"].replace(
                "[Validate and stop the exact launcher process group before "
                "manifest cleanup](docs/en/getting-started.md#manifest-scoped-cleanup)",
                "`./scripts/cosim_cleanup.sh --run-id <id> --confirm`",
                1,
            ),
        },
        {
            **documents,
            "README.zh.md": documents["README.zh.md"].replace(
                "[先校验并停止精确 launcher process group，再执行 manifest cleanup]"
                "(docs/zh/getting-started.md#manifest-scoped-cleanup)",
                "`./scripts/cosim_cleanup.sh --run-id <id> --confirm`",
                1,
            ),
        },
    )
    for index, mutated in enumerate(mutations, start=1):
        contract.require(
            mutated != documents,
            f"README cleanup mutation {index} fixture 缺失",
        )
        contract.require(
            bool(readme_cleanup_contract_issues(mutated)),
            f"README cleanup mutation {index} 未被拒绝",
        )


def check_public_commands(contract: Contract) -> None:
    policy_files = public_markdown_files() + [
        ROOT / "AGENTS.md",
        ROOT / "agents.md",
        *SKILL_MARKDOWN_FILES,
    ]
    for path in policy_files:
        for number, line in executable_fence_lines(path):
            contract.require(
                UNSAFE_COMMAND_RE.search(line) is None and
                SECRET_COMMAND_RE.search(line) is None,
                f"{path.relative_to(ROOT)}:{number} 含绕过 wrapper 或危险的可执行命令：{line}",
            )

    launcher = read_text(ROOT / "scripts" / "cosim_launch.sh")
    for stale in (
        "run_mi300x_fs.sh",
        "dd if=/root/roms/mi300.rom of=/dev/mem",
        "modprobe amdgpu ip_block_mask",
        "Manual setup (if service is not installed)",
    ):
        contract.require(stale not in launcher, f"cosim_launch.sh 仍包含过时提示：{stale}")

    result = subprocess.run(
        ["bash", "scripts/cosim_launch.sh", "--help"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    help_text = result.stdout + result.stderr
    contract.require(result.returncode == 0, "cosim_launch.sh --help 返回非零")
    contract.require(
        "/tmp/gem5-mi300x-<run-id>.sock" in help_text,
        "cosim_launch.sh --help 未说明 run-scoped socket 默认值",
    )
    contract.require(
        "/tmp/gem5-mi300x.sock" not in help_text,
        "cosim_launch.sh --help 仍展示固定 socket 默认值",
    )
    for required in (
        'GEM5_CONTAINER="$(cosim_container_name "$COSIM_RUN_ID")"',
        'GUEST_OVERLAY="${SESSION_DIR}/guest-overlay.qcow2"',
        'SOCKET_PATH="/tmp/gem5-mi300x-${COSIM_RUN_ID}.sock"',
        'SHMEM_PATH="/mi300x-vram-${COSIM_RUN_ID}"',
        'SHMEM_HOST_PATH="/cosim-guest-ram-${COSIM_RUN_ID}"',
        'manifest_init "$SESSION_DIR" "$COSIM_RUN_ID" "$COSIM_DIR"',
        "./scripts/cosim_build.sh guest",
        "不要手工写 /dev/mem",
        "全新会话",
    ):
        contract.require(required in launcher, f"cosim_launch.sh 缺少安全启动合同：{required}")

    runner_help = subprocess.run(
        ["bash", "scripts/run_cosim_tests.sh", "--help"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    runner_help_text = runner_help.stdout + runner_help.stderr
    contract.require(runner_help.returncode == 0, "run_cosim_tests.sh --help 返回非零")
    contract.require(
        "<artifact-directory>/qemu.log" in runner_help_text and
        "/tmp/qemu-cosim-tests.log" not in runner_help_text,
        "run_cosim_tests.sh --help 的 console log 默认路径与实际 artifact 不一致",
    )
    runner = read_text(ROOT / "scripts" / "run_cosim_tests.sh")
    try:
        interrupt_block = runner.split("on_interrupt() {", 1)[1].split(
            "on_exit() {", 1
        )[0]
    except IndexError:
        interrupt_block = ""
    contract.require(
        "--confirm" not in interrupt_block and
        "Console pipe: ${SESSION_FIFO}" in interrupt_block and
        "禁止对 live launcher 直接运行 cosim_cleanup.sh" in interrupt_block,
        "runner 的 keep-alive interrupt 提示必须给出 pipe 且不得建议直接 cleanup",
    )
    for required in (
        'ORIGINAL_ARGS=("$@")',
        '"${RUNNER_ARTIFACT_DIR}/runner-invocation.txt"',
        '${RUNNER_ARTIFACT_DIR}/launch-invocation.txt',
        'GUEST_SCRIPT_ARCHIVE="${RUNNER_ARTIFACT_DIR}/guest-run.sh"',
        'echo "boot_timeout=${BOOT_TIMEOUT_SECS}"',
        'echo "test_timeout=${TEST_TIMEOUT_SECS}"',
        'echo "guest_run_timeout=${GUEST_RUN_TIMEOUT_SECS}"',
        '--share-dir|--artifact-dir|--evidence-test-id|--evidence-token)',
        'evidence-boundaries "$GEM5_EVIDENCE"',
        'gem5_evidence_test_id=${TEST_NAME}',
        'gem5_evidence_token=${EVIDENCE_BOUNDARY_TOKEN}',
    ):
        contract.require(required in runner, f"run_cosim_tests.sh 缺少证据合同：{required}")

    verifier = read_text(ROOT / "scripts" / "verify_cosim_matrix.py")
    for required in (
        'SCHEMA = "cosim-matrix-verification/v2"',
        '"runner_invocation": artifact_dir / "runner-invocation.txt"',
        '"launch_invocation": artifact_dir / "launch-invocation.txt"',
        '"guest_script": artifact_dir / "guest-run.sh"',
        'render_guest_run_script(',
        'guest_script_bytes != expected_guest_script_bytes',
        'expected_test_timeout=expected_test_timeout',
    ):
        contract.require(required in verifier, f"严格 verifier 缺少合同：{required}")

    readme_documents = {
        path.name: read_text(path)
        for path in (ROOT / "README.md", ROOT / "README.zh.md")
    }
    contract.errors.extend(readme_cleanup_contract_issues(readme_documents))
    check_readme_cleanup_mutation_guard(contract)

    agents = read_text(ROOT / "agents.md")
    top_agents = read_text(ROOT / "AGENTS.md")
    for skill, route in ROUTE_CONTRACTS.items():
        contract.require((ROOT / route).is_file(), f"技能路由目标不存在：{route}")
        contract.require(
            re.search(rf"(?m)^- [^\n]*`{re.escape(route)}`[^\n]*$", agents) is not None,
            f"agents.md 缺少精确技能路由：{skill} -> {route}",
        )
    for text, name in ((agents, "agents.md"), (top_agents, "AGENTS.md")):
        contract.require("docs/en/" in text and "明确例外" in text,
                         f"{name} 未明确声明 docs/en/ 语言例外")
        contract.require("远端推送" in text and "明确" in text,
                         f"{name} 未保留远端推送的单独授权边界")
        contract.require("README.md" in text and "docs/README.md" in text,
                         f"{name} 未声明根英文 README 与双语索引的语言例外")
    contract.require(
        "只有用户单独明确要求推送并确认目标 remote/branch 后才能推送" in agents,
        "agents.md 未保留 remote/branch 级别的单独 push 授权",
    )
    contract.require(
        "远端推送必须由用户单独明确授权" in top_agents,
        "AGENTS.md 未保留单独 push 授权",
    )

    skill_contracts = {
        ROUTED_POLICY_FILES[1]: ("--cosim-backend", "legacy cosim", "manually load"),
        ROUTED_POLICY_FILES[2]: (
            "COSIM_GUEST_SUDO_PASSWORD",
            "dd if=/root/roms/mi300.rom",
            "modprobe amdgpu ip_block_mask",
            "/tmp/${SESSION_NAME}-${RUN_ID}.log",
        ),
        ROUTED_POLICY_FILES[3]: (
            "--args .local/cosim/qemu",
            "qemu-system-x86_64 [args...]",
        ),
        ROUTED_POLICY_FILES[4]: (
            "--hang-env",
            "HIP-Basic/FloydWarshall",
            "bash scripts/cosim_cleanup.sh",
        ),
    }
    for path, stale_values in skill_contracts.items():
        text = read_text(path)
        for stale in stale_values:
            contract.require(
                stale not in text,
                f"{path.relative_to(ROOT)} 仍包含陈旧或不安全入口：{stale}",
            )

    all_skill_text = "\n".join(read_text(path) for path in SKILL_MARKDOWN_FILES)
    for stale in ("--hang-env", "COSIM_GUEST_SUDO_PASSWORD", "sudo -S"):
        contract.require(stale not in all_skill_text, f"技能树仍包含陈旧或秘密注入入口：{stale}")

    guest_skill = read_text(ROUTED_POLICY_FILES[2])
    for required in (
        'label=io.cosim-gpu.run-id=${RUN_ID}',
        'kill -0 "$LAUNCH_PID"',
        "timeout 5s bash -c",
        "deadline=$((SECONDS + 30))",
    ):
        contract.require(required in guest_skill, f"Guest 技能缺少有界交互合同：{required}")
    contract.require(
        "while true; do" not in guest_skill and
        "--filter name=gem5-cosim" not in guest_skill,
        "Guest 技能仍包含无界等待或非 run-scoped container 查询",
    )

    live_wait = read_text(ROUTED_POLICY_FILES[6])
    for required in (
        "capture_wait_sample 1",
        "capture_wait_sample 2",
        "deadline=$((SECONDS + 60))",
        "__DMESG_FULL__",
        "__DMESG_FILTERED__",
        "target-before.log",
        "target-after.log",
    ):
        contract.require(required in live_wait, f"live-wait 缺少双样本合同：{required}")
    for issue in live_wait_contract_issues(live_wait):
        contract.errors.append(f"live-wait {issue}")
    check_live_wait_mutation_guard(contract, live_wait)

    discovery = read_text(
        ROOT / ".agents" / "skills" / "cosim-gpu-debug" / "references" /
        "gem5-model" / "discovery-log.md"
    )
    contract.require(
        "/dev/shm/cosim-guest-ram-<run-id>" in discovery,
        "discovery-log.md 未使用 run-scoped Guest RAM 名称",
    )


def check_evidence_contract(contract: Contract) -> None:
    documents = {
        str(path.relative_to(ROOT)): read_text(path)
        for path in (
            ZH_DIR / "getting-started.md",
            EN_DIR / "getting-started.md",
            ZH_DIR / "labs.md",
            EN_DIR / "labs.md",
            ZH_DIR / "reference.md",
            EN_DIR / "reference.md",
        )
    }
    contract.errors.extend(
        evidence_contract_issues(
            documents,
            read_text(ROOT / "scripts" / "run_cosim_tests.sh"),
            read_text(ROOT / "scripts" / "cosim_launch.sh"),
            read_text(ROOT / "scripts" / "verify_cosim_matrix.py"),
        )
    )
    check_evidence_contract_mutation_guard(contract)


def main() -> int:
    contract = Contract()
    check_pairs(contract)
    check_local_links(contract)
    check_labs(contract)
    check_issue_playbooks(contract)
    check_shell_and_source_contracts(contract)
    check_phase6_recovery_contract(contract)
    check_strict_acceptance_contract(contract)
    check_public_commands(contract)
    check_evidence_contract(contract)
    if contract.errors:
        print("[FAIL] 文档合同检查失败：", file=sys.stderr)
        for error in contract.errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print("[PASS] 双语配对、链接、实验结构、公开命令与代理规则合同均通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
