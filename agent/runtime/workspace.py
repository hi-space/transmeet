"""Per-session working directory for the agent's local tools.

Every tool resolves paths through `Workspace.resolve`, which refuses anything that
escapes the session root. Skills are exposed read-only through a `skills` symlink so
the model can `read_file skills/<name>/...` and `bash python3 skills/<name>/scripts/...`.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"
DEFAULT_WORK_BASE = Path(os.environ.get("WORK_BASE", "/tmp/transmeet-work"))
_SESSION_RE = re.compile(r"[^A-Za-z0-9_-]")


class ToolPathError(ValueError):
    """A tool was asked to touch a path outside the workspace."""


@dataclass(frozen=True)
class Workspace:
    root: Path
    skills_dir: Path

    def resolve(self, rel: str) -> Path:
        if not isinstance(rel, str) or not rel.strip():
            raise ToolPathError("path is required")
        candidate = Path(rel)
        if candidate.is_absolute():
            raise ToolPathError(f"absolute paths are not allowed: {rel}")
        target = (self.root / candidate).resolve()
        root = self.root.resolve()
        skills = self.skills_dir.resolve()
        if target == root or root in target.parents:
            return target
        if target == skills or skills in target.parents:
            return target
        raise ToolPathError(f"path escapes the workspace: {rel}")

    def read_text(self, rel: str) -> str:
        return self.resolve(rel).read_text(encoding="utf-8")

    def write_text(self, rel: str, content: str) -> Path:
        path = self.resolve(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def write_json(self, rel: str, obj: Any) -> Path:
        return self.write_text(rel, json.dumps(obj, ensure_ascii=False, indent=1))

    def read_json(self, rel: str) -> Any:
        return json.loads(self.read_text(rel))

    def exists(self, rel: str) -> bool:
        try:
            return self.resolve(rel).exists()
        except ToolPathError:
            return False


def create_workspace(session_id: str, *, base: Path | None = None, skills_dir: Path | None = None) -> Workspace:
    safe = _SESSION_RE.sub("_", session_id)[:96] or "session"
    root = (base or DEFAULT_WORK_BASE) / safe
    root.mkdir(parents=True, exist_ok=True)
    (root / "diagrams").mkdir(exist_ok=True)
    skills = (skills_dir or Path(os.environ.get("SKILLS_DIR", str(DEFAULT_SKILLS_DIR)))).resolve()
    link = root / "skills"
    if not link.exists():
        link.symlink_to(skills, target_is_directory=True)
    return Workspace(root=root, skills_dir=skills)
