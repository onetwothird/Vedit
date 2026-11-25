"""Cloud (multi-user web) settings. Absent unless VEDIT_CLOUD=1, so the desktop engine is unaffected."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from scripts.cloud_storage import Storage


@dataclass
class CloudConfig:
    data_root: Path
    origins: tuple[str, ...]
    supabase_url: str
    jwt_secret: str | None
    storage: "Storage | None" = None  # R2 in production; wired in from_env by the deploy task
    usage: Any = None  # Usage protocol: added in the usage task
    runner_factory: Callable | None = None
    paused: bool = False


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
    )
