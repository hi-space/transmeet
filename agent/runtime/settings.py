"""Runtime configuration from environment variables (set on the AgentCore Runtime)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .workspace import DEFAULT_SKILLS_DIR, DEFAULT_WORK_BASE


@dataclass(frozen=True)
class Settings:
    region: str = "us-east-1"
    pages_bucket: str = ""
    pages_base_url: str = "https://transmeet.hi-yoo.com/p"
    meetings_table: str = "transmeet-meetings"
    gateway_url: str = ""
    page_max_bytes: int = 1_500_000
    skills_dir: Path = DEFAULT_SKILLS_DIR
    work_base: Path = DEFAULT_WORK_BASE
    budget_s: float = 2400.0
    tz_name: str = "Asia/Seoul"

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        return cls(
            region=env.get("REGION") or env.get("AWS_REGION") or "us-east-1",
            pages_bucket=env.get("PAGES_BUCKET", ""),
            pages_base_url=env.get("PAGES_PUBLIC_BASE_URL", cls.pages_base_url),
            meetings_table=env.get("MEETINGS_TABLE", cls.meetings_table),
            gateway_url=env.get("WEB_SEARCH_GATEWAY_URL", ""),
            page_max_bytes=int(env.get("PAGE_MAX_BYTES", cls.page_max_bytes)),
            skills_dir=Path(env.get("SKILLS_DIR", str(DEFAULT_SKILLS_DIR))),
            work_base=Path(env.get("WORK_BASE", str(DEFAULT_WORK_BASE))),
            budget_s=float(env.get("GRAPH_BUDGET_S", cls.budget_s)),
            tz_name=env.get("PAGE_TIMEZONE", cls.tz_name),
        )
