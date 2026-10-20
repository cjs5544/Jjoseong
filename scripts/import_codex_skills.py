#!/usr/bin/env python3
"""Codex/Agents 스킬을 Claude Code 개인 스킬 경로(~/.claude/skills)로 복사한다.

- 원본: ~/.agents/skills, ~/.codex/skills, ~/.codex/plugins/cache
- SKILL.md 가 있는 폴더 전체를 복사 (원본 유지, 심볼릭 링크는 실제 대상을 복사)
- 이름이 중복되는 스킬, 이미 대상에 있는 스킬은 덮어쓰지 않고 보고
- 플러그인 캐시에 여러 버전이 있으면 최신 버전만 사용
- Codex 전용 도구/MCP 가 필요한 스킬은 ~/.claude/skills-codex-only 로 따로 분류
- 각 스킬이 Claude 에서 실행 가능한지 점검한 결과를 ~/.claude/skill-import-report.md 에 기록

사용법:
    python import_codex_skills.py            # 실제 복사
    python import_codex_skills.py --dry-run  # 복사 없이 결과만 확인
"""
import argparse
import os
import re
import shutil
import sys
from pathlib import Path

HOME = Path.home()
SOURCES = [
    ("agents", HOME / ".agents" / "skills"),
    ("codex", HOME / ".codex" / "skills"),
    ("plugin-cache", HOME / ".codex" / "plugins" / "cache"),
]
DEST = HOME / ".claude" / "skills"
CODEX_ONLY_DEST = HOME / ".claude" / "skills-codex-only"
REPORT = HOME / ".claude" / "skill-import-report.md"

VERSION_RE = re.compile(r"^v?\d+(\.\d+)*([-+._][0-9A-Za-z.\-]+)?$")
HASH_RE = re.compile(r"^[0-9a-f]{7,64}$")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

# Codex 전용 도구 / 설정 흔적 (패턴, 설명)
CODEX_MARKERS = [
    (re.compile(r"\bapply_patch\b"), "Codex apply_patch 도구"),
    (re.compile(r"\bupdate_plan\b"), "Codex update_plan 도구"),
    (re.compile(r"\bview_image\b"), "Codex view_image 도구"),
    (re.compile(r"\bcontainer\.exec\b|\blocal_shell\b"), "Codex 셸 도구"),
    (re.compile(r"\$CODEX_HOME|~/\.codex|\.codex/"), "Codex 설정 경로 참조"),
    (re.compile(r"\bcodex (exec|mcp|login|resume)\b"), "Codex CLI 명령"),
]
MCP_MARKERS = [
    (re.compile(r"\bmcp__[A-Za-z0-9_-]+"), "MCP 도구 호출"),
    (re.compile(r"\bmcp_servers\b|\[mcp_servers"), "MCP 서버 설정"),
    (re.compile(r"\bMCP (server|tool)s?\b", re.I), "MCP 서버/도구 필요"),
]
SCAN_EXT = {".md", ".txt", ".yaml", ".yml", ".toml", ".json", ".py", ".sh", ".js", ".ts", ".ps1"}


def is_skill_dir(p: Path) -> bool:
    return (p / "SKILL.md").is_file()


def find_skills(root: Path):
    """root 아래에서 SKILL.md 를 가진 폴더를 찾는다. 스킬 폴더 안쪽은 더 내려가지 않는다."""
    found, seen = [], set()
    if not root.exists():
        return found
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            real = d.resolve()
        except OSError:
            continue
        if real in seen:  # 심볼릭 링크 순환 방지
            continue
        seen.add(real)
        if is_skill_dir(d):
            found.append(d)
            continue
        try:
            children = sorted(d.iterdir())
        except OSError:
            continue
        stack.extend(c for c in children if c.is_dir())
    return found


def parse_frontmatter(skill_md: Path) -> dict:
    text = skill_md.read_text(encoding="utf-8", errors="replace")
    m = re.match(r"^﻿?---\s*\n(.*?)\n---\s*(\n|$)", text, re.S)
    meta = {}
    if not m:
        return meta
    for line in m.group(1).splitlines():
        kv = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if kv:
            meta[kv.group(1)] = kv.group(2).strip().strip("'\"")
    return meta


def version_key(seg: str):
    nums = [int(n) for n in re.findall(r"\d+", seg)]
    return tuple(nums)


def latest_plugin_versions(skills, cache_root: Path):
    """플러그인 캐시에서 같은 스킬의 여러 버전 중 최신만 남긴다."""
    groups = {}
    for s in skills:
        parts = s.relative_to(cache_root).parts
        idx = next((i for i, p in enumerate(parts[:-1]) if VERSION_RE.match(p) or HASH_RE.match(p)), None)
        if idx is None:
            key, ver = parts, None
        else:
            key, ver = parts[:idx] + ("*",) + parts[idx + 1:], parts[idx]
        groups.setdefault(key, []).append((ver, s))

    keep, dropped = [], []
    for items in groups.values():
        def rank(item):
            ver, path = item
            semver = version_key(ver) if ver and VERSION_RE.match(ver) else ()
            return (semver, (path / "SKILL.md").stat().st_mtime)
        items.sort(key=rank, reverse=True)
        keep.append(items[0][1])
        dropped.extend((path, items[0][1]) for _, path in items[1:])
    return keep, dropped


def iter_files(d: Path):
    for root, _, files in os.walk(d, followlinks=True):
        for f in files:
            yield Path(root) / f


def check_skill(d: Path, meta: dict):
    """(Codex/MCP 의존 사유 목록, Claude 호환성 문제 목록)"""
    deps, issues = [], []
    if (d / "agents" / "openai.yaml").exists():
        txt = (d / "agents" / "openai.yaml").read_text(encoding="utf-8", errors="replace")
        if re.search(r"\bmcp\b|dependencies", txt, re.I):
            deps.append("agents/openai.yaml 에 MCP/도구 의존성 선언")
    for f in iter_files(d):
        if f.suffix.lower() not in SCAN_EXT:
            continue
        try:
            txt = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = f.relative_to(d).as_posix()
        for pat, why in CODEX_MARKERS + MCP_MARKERS:
            m = pat.search(txt)
            if m:
                deps.append(f"{why} ({rel}: `{m.group(0)}`)")

    name, desc = meta.get("name", ""), meta.get("description", "")
    if not name:
        issues.append("frontmatter 에 name 없음")
    elif not NAME_RE.match(name):
        issues.append(f"name '{name}' 형식 오류(소문자·숫자·하이픈, 64자 이하)")
    if not desc:
        issues.append("frontmatter 에 description 없음")
    elif len(desc) > 1024:
        issues.append("description 1024자 초과")

    body = (d / "SKILL.md").read_text(encoding="utf-8", errors="replace")
    for ref in set(re.findall(r"(?:scripts|references|assets)/[\w./-]+", body)):
        if not (d / ref.rstrip(".")).exists():
            issues.append(f"참조 파일 없음: {ref}")
    for f in iter_files(d):
        interp = {".py": "python3", ".js": "node", ".ts": "node", ".sh": "bash", ".ps1": "pwsh"}.get(f.suffix)
        if interp and not (shutil.which(interp) or (interp == "python3" and shutil.which("python"))):
            issues.append(f"실행기 '{interp}' 없음 ({f.name})")
    return sorted(set(deps)), sorted(set(issues))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="복사하지 않고 결과만 보고")
    args = ap.parse_args()

    candidates, skipped_versions = [], []
    for label, root in SOURCES:
        skills = find_skills(root)
        if label == "plugin-cache" and skills:
            skills, dropped = latest_plugin_versions(skills, root)
            skipped_versions += dropped
        candidates += [(label, s) for s in skills]

    copied, codex_only, duplicates, existing, missing = [], [], [], [], []
    for label, root in SOURCES:
        if not root.exists():
            missing.append(str(root))

    seen = {}
    for label, src in candidates:
        meta = parse_frontmatter(src / "SKILL.md")
        name = meta.get("name") or src.name
        folder = name if NAME_RE.match(name) else src.name
        if folder in seen:
            duplicates.append((folder, src, seen[folder]))
            continue
        seen[folder] = src

        deps, issues = check_skill(src, meta)
        target_root = CODEX_ONLY_DEST if deps else DEST
        target = target_root / folder
        if target.exists() or (DEST / folder).exists() or (CODEX_ONLY_DEST / folder).exists():
            existing.append((folder, src, target))
            continue
        if not args.dry_run:
            target_root.mkdir(parents=True, exist_ok=True)
            # symlinks=False: 링크는 실제 대상 파일/폴더 내용을 복사
            shutil.copytree(src.resolve(), target, symlinks=False)
        (codex_only if deps else copied).append((folder, label, src, target, deps, issues))

    lines = ["# Codex → Claude 스킬 가져오기 결과", ""]
    if args.dry_run:
        lines += ["> **dry-run**: 실제 복사는 하지 않았습니다.", ""]
    if missing:
        lines += ["## 없는 원본 경로", *[f"- `{m}`" for m in missing], ""]

    lines += [f"## Claude 에서 바로 사용 가능 ({len(copied)}개) → `{DEST}`"]
    for folder, label, src, _, _, issues in copied:
        status = "✅ 실행 가능" if not issues else "⚠️ 수정 필요: " + "; ".join(issues)
        lines.append(f"- **{folder}** ({label}) — {status}  \n  원본: `{src}`")
    lines.append("")

    lines += [f"## Codex 전용 도구/MCP 필요 ({len(codex_only)}개) → `{CODEX_ONLY_DEST}`",
              "Claude 가 자동으로 불러오지 않는 폴더에 두었습니다. 의존성을 Claude 도구로 바꾸거나 "
              "같은 MCP 서버를 Claude 에 연결한 뒤 `~/.claude/skills` 로 옮기세요.", ""]
    for folder, label, src, _, deps, issues in codex_only:
        lines.append(f"- **{folder}** ({label}) — ❌ 그대로는 실행 불가  \n  원본: `{src}`")
        lines += [f"  - 의존: {d}" for d in deps]
        lines += [f"  - 형식 문제: {i}" for i in issues]
    lines.append("")

    if existing:
        lines += ["## 이미 대상에 있어 건너뜀 (덮어쓰지 않음)",
                  *[f"- **{f}**: 원본 `{s}` / 기존 `{t}`" for f, s, t in existing], ""]
    if duplicates:
        lines += ["## 중복 스킬 (먼저 찾은 것만 복사)",
                  *[f"- **{f}**: 건너뜀 `{s}` (복사한 것: `{k}`)" for f, s, k in duplicates], ""]
    if skipped_versions:
        lines += ["## 플러그인 캐시 구버전 (최신만 사용)",
                  *[f"- 건너뜀 `{old}` → 사용 `{new}`" for old, new in skipped_versions], ""]

    report = "\n".join(lines)
    print(report)
    if not args.dry_run:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(report, encoding="utf-8")
        print(f"\n보고서 저장: {REPORT}")


if __name__ == "__main__":
    sys.exit(main())
