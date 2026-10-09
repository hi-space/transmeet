"""Local tools the model can call inside the Runtime session (file I/O, shell, skill loading).

Tools never raise: failures come back as `error: ...` strings so the model can correct
itself. All paths go through `Workspace.resolve` (no escaping the session directory).
"""
from __future__ import annotations

import os
import subprocess
from typing import Iterable, List

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from .workspace import ToolPathError, Workspace

SKILL_NAMES = ("meeting-minutes-page", "meeting-research", "meeting-insights", "meeting-diagrams")
BASH_OUTPUT_LIMIT = 20_000
BASH_DEFAULT_TIMEOUT_S = 120
BASH_MAX_TIMEOUT_S = 600
READ_LIMIT = 400_000


class ReadFileArgs(BaseModel):
    path: str = Field(description="작업 디렉터리 기준 상대 경로 (예: research.html, skills/meeting-research/SKILL.md)")


class WriteFileArgs(BaseModel):
    path: str = Field(description="작업 디렉터리 기준 상대 경로")
    content: str = Field(description="파일 전체 내용 (기존 파일은 덮어쓴다)")


class EditFileArgs(BaseModel):
    path: str = Field(description="작업 디렉터리 기준 상대 경로")
    old: str = Field(description="바꿀 문자열. 파일 안에 정확히 한 번 있어야 한다")
    new: str = Field(description="새 문자열")


class BashArgs(BaseModel):
    command: str = Field(description="작업 디렉터리에서 실행할 bash 명령")
    timeout_s: int = Field(default=BASH_DEFAULT_TIMEOUT_S, ge=1, le=BASH_MAX_TIMEOUT_S, description="제한 시간(초)")


class LoadSkillArgs(BaseModel):
    name: str = Field(description="스킬 이름: " + ", ".join(SKILL_NAMES))


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    dropped = len(text) - limit
    return text[:half] + f"\n[... truncated {dropped} chars ...]\n" + text[-half:]


def make_tools(ws: Workspace, names: Iterable[str]) -> List[BaseTool]:
    wanted = list(names)

    def read_file(path: str) -> str:
        try:
            return _truncate(ws.read_text(path), READ_LIMIT)
        except ToolPathError as e:
            return f"error: {e}"
        except FileNotFoundError:
            return f"error: file not found: {path}"
        except UnicodeDecodeError:
            return f"error: not a text file: {path}"

    def write_file(path: str, content: str) -> str:
        try:
            p = ws.write_text(path, content)
            return f"wrote {len(content.encode('utf-8'))} bytes to {p.relative_to(ws.root.resolve()) if p.is_relative_to(ws.root.resolve()) else path}"
        except ToolPathError as e:
            return f"error: {e}"
        except OSError as e:
            return f"error: {e}"

    def edit_file(path: str, old: str, new: str) -> str:
        try:
            text = ws.read_text(path)
        except ToolPathError as e:
            return f"error: {e}"
        except FileNotFoundError:
            return f"error: file not found: {path}"
        if not old:
            return "error: old must not be empty"
        count = text.count(old)
        if count == 0:
            return "error: old string not found in file (0 matches)"
        if count > 1:
            return f"error: old string matches {count} times; include more context so it matches exactly once"
        ws.write_text(path, text.replace(old, new, 1))
        return f"ok: replaced 1 occurrence in {path}"

    def bash(command: str, timeout_s: int = BASH_DEFAULT_TIMEOUT_S) -> str:
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"}
        try:
            proc = subprocess.run(
                ["bash", "-lc", command], cwd=ws.root, capture_output=True, text=True,
                timeout=timeout_s, env=env, errors="replace",
            )
        except subprocess.TimeoutExpired:
            return f"error: command timed out after {timeout_s}s"
        except OSError as e:
            return f"error: {e}"
        out = proc.stdout
        if proc.stderr:
            out += ("\n" if out else "") + "[stderr]\n" + proc.stderr
        if proc.returncode != 0:
            out += f"\n[exit code {proc.returncode}]"
        return _truncate(out, BASH_OUTPUT_LIMIT) if out else "(no output)"

    def load_skill(name: str) -> str:
        if name not in SKILL_NAMES:
            return f"error: unknown skill '{name}'. available: {', '.join(SKILL_NAMES)}"
        base = ws.skills_dir / name
        try:
            body = (base / "SKILL.md").read_text(encoding="utf-8")
        except FileNotFoundError:
            return f"error: skill '{name}' has no SKILL.md"
        files = []
        for sub in ("references", "scripts", "assets"):
            d = base / sub
            if d.is_dir():
                files.extend(f"skills/{name}/{sub}/{p.name}" for p in sorted(d.iterdir()) if p.is_file())
        listing = "\n".join(f"- {f}" for f in files) or "- (none)"
        return f"{body}\n\n## 번들 파일 (read_file / bash 에 이 경로를 그대로 쓴다)\n{listing}\n"

    registry = {
        "read_file": StructuredTool.from_function(
            func=read_file, name="read_file", args_schema=ReadFileArgs,
            description="작업 디렉터리 안의 텍스트 파일을 읽는다. 스킬 파일은 skills/<이름>/... 경로로 읽는다.",
        ),
        "write_file": StructuredTool.from_function(
            func=write_file, name="write_file", args_schema=WriteFileArgs,
            description="작업 디렉터리 안에 파일을 쓴다(덮어쓰기). 하위 디렉터리는 자동 생성된다.",
        ),
        "edit_file": StructuredTool.from_function(
            func=edit_file, name="edit_file", args_schema=EditFileArgs,
            description="파일 안에서 정확히 한 번 나오는 문자열을 다른 문자열로 바꾼다. 큰 파일을 고칠 때 전체를 다시 쓰지 말고 이 도구를 쓴다.",
        ),
        "bash": StructuredTool.from_function(
            func=bash, name="bash", args_schema=BashArgs,
            description="작업 디렉터리에서 bash 명령을 실행하고 stdout/stderr 를 돌려준다(최대 20KB). python3 가 있다. 웹 검색은 web_search 도구를 쓴다.",
        ),
        "load_skill": StructuredTool.from_function(
            func=load_skill, name="load_skill", args_schema=LoadSkillArgs,
            description="스킬의 SKILL.md 와 번들 파일 목록을 돌려준다. 작업을 시작할 때 해당 스킬을 먼저 로드한다.",
        ),
    }
    unknown = [n for n in wanted if n not in registry]
    if unknown:
        raise KeyError(f"unknown tool names: {unknown}")
    return [registry[n] for n in wanted]
