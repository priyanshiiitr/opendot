"""Path-safe workspace / artifact file operations for one computer."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

ALLOWED_SUFFIXES = frozenset({".md", ".txt", ".json", ".csv", ".png", ".html"})
SAFE_NAME = re.compile(r"^[A-Za-z0-9._\- ]{1,120}$")


class WorkspaceError(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class WorkspaceStore:
    def __init__(self, workspace: Path, artifacts: Path, *, max_file_bytes: int = 256_000):
        self.workspace = workspace.resolve()
        self.artifacts = artifacts.resolve()
        self.max_file_bytes = max_file_bytes
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.artifacts.mkdir(parents=True, exist_ok=True)

    def _resolve(self, root: Path, relative: str) -> Path:
        rel = (relative or "").replace("\\", "/").lstrip("/")
        if not rel or ".." in Path(rel).parts:
            raise WorkspaceError("Path traversal is not allowed.")
        if Path(rel).is_absolute():
            raise WorkspaceError("Absolute paths are not allowed.")
        target = (root / rel).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise WorkspaceError("Path escapes the authorized directory.") from exc
        # Reject symlink escapes when the file already exists.
        if target.exists() and target.is_symlink():
            raise WorkspaceError("Symlink paths are not allowed.")
        return target

    def list_files(self, *, area: str = "workspace") -> list[dict[str, Any]]:
        root = self.artifacts if area == "artifacts" else self.workspace
        items: list[dict[str, Any]] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            rel = str(path.relative_to(root)).replace("\\", "/")
            items.append({"path": rel, "bytes": path.stat().st_size, "suffix": path.suffix.lower()})
        return items

    def read_text(self, relative: str, *, area: str = "workspace") -> dict[str, Any]:
        root = self.artifacts if area == "artifacts" else self.workspace
        path = self._resolve(root, relative)
        if path.suffix.lower() not in {".md", ".txt", ".json", ".csv", ".html"}:
            raise WorkspaceError("Unsupported file type for reading.")
        if not path.is_file():
            raise WorkspaceError("File not found.")
        data = path.read_bytes()
        if len(data) > self.max_file_bytes:
            raise WorkspaceError("File exceeds the configured size limit.")
        return {"path": relative.replace("\\", "/"), "content": data.decode("utf-8", errors="replace"), "bytes": len(data)}

    def write_text(
        self,
        relative: str,
        content: str,
        *,
        area: str = "workspace",
        overwrite: bool = False,
    ) -> dict[str, Any]:
        root = self.artifacts if area == "artifacts" else self.workspace
        name = Path(relative.replace("\\", "/")).name
        if not SAFE_NAME.match(name):
            raise WorkspaceError("Unsafe file name.")
        if Path(relative).suffix.lower() not in ALLOWED_SUFFIXES:
            raise WorkspaceError("Unsupported file type for writing.")
        path = self._resolve(root, relative)
        encoded = content.encode("utf-8")
        if len(encoded) > self.max_file_bytes:
            raise WorkspaceError("Content exceeds the configured size limit.")
        if path.exists() and not overwrite:
            raise WorkspaceError("File already exists. Pass overwrite=true after approval to replace it.")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encoded)
        return {"path": relative.replace("\\", "/"), "bytes": len(encoded), "overwrote": overwrite and path.exists()}

    def write_csv(self, relative: str, rows: list[dict[str, Any]], *, overwrite: bool = False) -> dict[str, Any]:
        if not rows:
            raise WorkspaceError("CSV requires at least one row.")
        fieldnames = list(rows[0].keys())
        from io import StringIO

        buf = StringIO()
        writer = csv.DictWriter(buf, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows[:500]:
            writer.writerow({k: row.get(k, "") for k in fieldnames})
        return self.write_text(relative, buf.getvalue(), area="workspace", overwrite=overwrite)

    def write_json(self, relative: str, data: Any, *, overwrite: bool = False) -> dict[str, Any]:
        return self.write_text(relative, json.dumps(data, ensure_ascii=False, indent=2), area="workspace", overwrite=overwrite)

    def absolute_for_download(self, relative: str, *, area: str = "artifacts") -> Path:
        root = self.artifacts if area == "artifacts" else self.workspace
        path = self._resolve(root, relative)
        if not path.is_file():
            raise WorkspaceError("File not found.")
        return path
