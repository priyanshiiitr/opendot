"""Skill install registry — skills never grant their own tool permissions."""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..database import SkillInstall
from .loader import SkillError, SkillPackage, discover_skills, load_skill_dir


class SkillRegistry:
    def __init__(self, skills_root: Path):
        self.skills_root = skills_root
        self.skills_root.mkdir(parents=True, exist_ok=True)

    def catalog(self) -> list[SkillPackage]:
        return discover_skills(self.skills_root)

    def get_package(self, skill_id: str) -> SkillPackage:
        path = self.skills_root / skill_id
        return load_skill_dir(path)

    def ensure_builtin_seeded(self) -> None:
        """Copy bundled examples into the writable skills root if missing."""
        bundled = Path(__file__).resolve().parent / "builtin"
        if not bundled.is_dir():
            return
        for child in bundled.iterdir():
            if child.is_dir() and (child / "SKILL.md").is_file():
                dest = self.skills_root / child.name
                if not dest.exists():
                    shutil.copytree(child, dest)

    def install(
        self,
        db: Session,
        package: SkillPackage,
        *,
        teammate_id: int | None = None,
        reviewed: bool = False,
        enabled: bool = False,
    ) -> SkillInstall:
        row = SkillInstall(
            id=str(uuid.uuid4()),
            skill_id=package.skill_id,
            version=package.version,
            name=package.name,
            enabled=enabled and reviewed,
            teammate_id=teammate_id,
            package_path=package.package_path,
            allowed_tool_categories_json=json.dumps(package.allowed_tool_categories),
            reviewed=reviewed,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    def list_installs(self, db: Session, teammate_id: int | None = None) -> list[SkillInstall]:
        q = db.query(SkillInstall)
        if teammate_id is not None:
            q = q.filter((SkillInstall.teammate_id == teammate_id) | (SkillInstall.teammate_id.is_(None)))
        return q.order_by(SkillInstall.updated_at.desc()).all()

    def set_enabled(self, db: Session, install_id: str, enabled: bool) -> SkillInstall:
        row = db.get(SkillInstall, install_id)
        if not row:
            raise SkillError("Skill install not found.")
        if enabled and not row.reviewed:
            raise SkillError("Skill must be reviewed before enabling.")
        row.enabled = enabled
        db.commit()
        db.refresh(row)
        return row

    def mark_reviewed(self, db: Session, install_id: str) -> SkillInstall:
        row = db.get(SkillInstall, install_id)
        if not row:
            raise SkillError("Skill install not found.")
        row.reviewed = True
        db.commit()
        db.refresh(row)
        return row

    def resolve_enabled(self, db: Session, skill_id: str, teammate_id: int | None = None) -> tuple[SkillInstall, SkillPackage]:
        q = db.query(SkillInstall).filter(
            SkillInstall.skill_id == skill_id,
            SkillInstall.enabled.is_(True),
            SkillInstall.reviewed.is_(True),
        )
        if teammate_id is not None:
            q = q.filter((SkillInstall.teammate_id == teammate_id) | (SkillInstall.teammate_id.is_(None)))
        row = q.order_by(SkillInstall.updated_at.desc()).first()
        if not row:
            raise SkillError("No enabled reviewed skill install for this id.")
        package = load_skill_dir(Path(row.package_path))
        if package.version != row.version:
            # Pin meaning of past installs: prefer disk but report version used at install time in workflows.
            pass
        return row, package

    @staticmethod
    def public_install(row: SkillInstall) -> dict[str, Any]:
        return {
            "id": row.id,
            "skill_id": row.skill_id,
            "version": row.version,
            "name": row.name,
            "enabled": row.enabled,
            "teammate_id": row.teammate_id,
            "package_path": row.package_path,
            "allowed_tool_categories": json.loads(row.allowed_tool_categories_json or "[]"),
            "reviewed": row.reviewed,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    @staticmethod
    def public_package(pkg: SkillPackage) -> dict[str, Any]:
        return {
            "skill_id": pkg.skill_id,
            "version": pkg.version,
            "name": pkg.name,
            "description": pkg.description,
            "when_to_use": pkg.when_to_use,
            "required_inputs": pkg.required_inputs,
            "expected_outputs": pkg.expected_outputs,
            "allowed_tool_categories": pkg.allowed_tool_categories,
            "required_permissions": pkg.required_permissions,
            "constraints": pkg.constraints,
            "evaluation_criteria": pkg.evaluation_criteria,
            "instructions_md": pkg.instructions_md,
            "package_path": pkg.package_path,
        }
