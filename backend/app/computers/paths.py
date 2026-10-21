"""Filesystem roots for per-computer profile / workspace / artifacts."""

from __future__ import annotations

import os
from pathlib import Path


def default_computers_root(configured: str = "") -> Path:
    if configured.strip():
        return Path(configured).expanduser().resolve()
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return (Path(local) / "AITeammates" / "computers").resolve()
    return (Path.cwd() / "data" / "computers").resolve()


def computer_layout(root: Path, computer_id: str) -> dict[str, Path]:
    base = (root / computer_id).resolve()
    return {
        "root": base,
        "profile": base / "profile",
        "workspace": base / "workspace",
        "artifacts": base / "artifacts",
        "metadata": base / "metadata",
        "preview": base / "artifacts" / "preview.png",
    }


def ensure_layout(paths: dict[str, Path]) -> None:
    for key in ("root", "profile", "workspace", "artifacts", "metadata"):
        paths[key].mkdir(parents=True, exist_ok=True)
