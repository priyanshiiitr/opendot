"""Load and validate versioned local skill packages (untrusted until reviewed)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_PACKAGE_BYTES = 200_000
MAX_SKILL_MD_BYTES = 80_000
SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9\-_]{1,80}$")


class SkillError(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class SkillPackage:
    skill_id: str
    version: str
    name: str
    description: str
    when_to_use: str
    required_inputs: list[str]
    expected_outputs: list[str]
    allowed_tool_categories: list[str]
    required_permissions: list[str]
    constraints: list[str]
    evaluation_criteria: list[str]
    instructions_md: str
    package_path: str
    metadata: dict[str, Any]


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta_raw = parts[1].strip()
    body = parts[2].lstrip("\n")
    # Minimal YAML-ish: key: value or key: [a, b]
    meta: dict[str, Any] = {}
    for line in meta_raw.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            meta[key] = [x.strip().strip("\"'") for x in inner.split(",") if x.strip()] if inner else []
        else:
            meta[key] = value.strip("\"'")
    return meta, body


def load_skill_dir(path: Path) -> SkillPackage:
    root = path.resolve()
    if not root.is_dir():
        raise SkillError("Skill package directory not found.")
    # Size guard
    total = 0
    for p in root.rglob("*"):
        if p.is_file():
            total += p.stat().st_size
            if total > MAX_PACKAGE_BYTES:
                raise SkillError("Skill package exceeds size limit.")
            # Reject path tricks
            try:
                p.resolve().relative_to(root)
            except ValueError as exc:
                raise SkillError("Skill package path escape detected.") from exc
            if p.is_symlink():
                raise SkillError("Symlinks are not allowed in skill packages.")
    skill_md = root / "SKILL.md"
    if not skill_md.is_file():
        raise SkillError("SKILL.md is required.")
    raw = skill_md.read_text(encoding="utf-8")
    if len(raw.encode("utf-8")) > MAX_SKILL_MD_BYTES:
        raise SkillError("SKILL.md exceeds size limit.")
    meta, body = _parse_frontmatter(raw)
    # Optional metadata.json overrides / supplements
    meta_json_path = root / "metadata.json"
    if meta_json_path.is_file():
        try:
            meta.update(json.loads(meta_json_path.read_text(encoding="utf-8")))
        except json.JSONDecodeError as exc:
            raise SkillError("metadata.json is invalid JSON.") from exc
    skill_id = str(meta.get("id") or root.name)
    if not SAFE_ID.match(skill_id):
        raise SkillError("Invalid skill id.")
    version = str(meta.get("version") or "0.1.0")
    name = str(meta.get("name") or skill_id)
    return SkillPackage(
        skill_id=skill_id,
        version=version,
        name=name,
        description=str(meta.get("description") or ""),
        when_to_use=str(meta.get("when_to_use") or ""),
        required_inputs=list(meta.get("required_inputs") or []),
        expected_outputs=list(meta.get("expected_outputs") or []),
        allowed_tool_categories=list(meta.get("allowed_tool_categories") or ["browser", "workspace", "local"]),
        required_permissions=list(meta.get("required_permissions") or []),
        constraints=list(meta.get("constraints") or []),
        evaluation_criteria=list(meta.get("evaluation_criteria") or []),
        instructions_md=body,
        package_path=str(root),
        metadata=meta,
    )


def discover_skills(skills_root: Path) -> list[SkillPackage]:
    if not skills_root.is_dir():
        return []
    found: list[SkillPackage] = []
    for child in sorted(skills_root.iterdir()):
        if child.is_dir() and (child / "SKILL.md").is_file():
            try:
                found.append(load_skill_dir(child))
            except SkillError:
                continue
    return found
