"""Strict page validator used by the publish node.

The single source of truth is the skill script
`skills/meeting-minutes-page/scripts/validate_page.py` — the same file the model runs
with `bash` before handing the page over — so a page that passes the agent's own check
also passes here. We load that script as a module instead of copying its code.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import ModuleType
from typing import List

from .workspace import DEFAULT_SKILLS_DIR

_SCRIPT_REL = Path("meeting-minutes-page") / "scripts" / "validate_page.py"
_module: ModuleType | None = None


def _load() -> ModuleType:
    global _module
    if _module is None:
        skills = Path(os.environ.get("SKILLS_DIR", str(DEFAULT_SKILLS_DIR)))
        path = skills / _SCRIPT_REL
        spec = importlib.util.spec_from_file_location("transmeet_validate_page", path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"validator script not found: {path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _module = mod
    return _module


def validate_html(html: str, max_bytes: int = 1_500_000) -> List[str]:
    return list(_load()._validate(html, max_bytes))


def __getattr__(name: str):
    if name in ("REQUIRED_SECTIONS", "REQUIRED_META", "MAX_BYTES"):
        return getattr(_load(), name)
    raise AttributeError(name)
