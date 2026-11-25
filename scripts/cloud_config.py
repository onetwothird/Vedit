"""Cloud (multi-user web) settings. Absent unless VEDIT_CLOUD=1, so the desktop engine is unaffected."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from scripts.cloud_storage import Storage
    from scripts.cloud_usage import Usage


@dataclass
class CloudConfig:
    data_root: Path
    origins: tuple[str, ...]
    supabase_url: str
    jwt_secret: str | None
    storage: "Storage | None" = None  # R2 in production; wired in from_env by the deploy task
    usage: "Usage | None" = None  # SupabaseUsage in production; wired in from_env by the deploy task
    runner_factory: Callable | None = None
    paused: bool = False
    sweep_token: str | None = None  # VEDIT_SWEEP_TOKEN: lets the daily cron start the retention sweep (route is 404 without)
    commit: Callable[[], None] | None = None  # the Modal Volume's commit, run after the sweep


def from_env() -> CloudConfig | None:
    if os.environ.get("VEDIT_CLOUD") != "1":
        return None
    origins = tuple(o.strip() for o in os.environ.get("VEDIT_ORIGINS", "").split(",") if o.strip())
    return CloudConfig(
        data_root=Path(os.environ.get("VEDIT_DATA_ROOT", "/data")),
        origins=origins,
        supabase_url=os.environ.get("SUPABASE_URL", "").rstrip("/"),
        jwt_secret=os.environ.get("SUPABASE_JWT_SECRET") or None,
        paused=os.environ.get("VEDIT_PAUSED") == "1",
        sweep_token=os.environ.get("VEDIT_SWEEP_TOKEN") or None,
    )
