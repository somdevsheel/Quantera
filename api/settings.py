"""Runtime settings, from environment variables.

QUANTERA_RELEASE      release tag to serve (default: the most recently frozen release)
QUANTERA_API_MODE     internal_research (default) | redistribution   (docs/api_design.md §5)
QUANTERA_DRIFT_CHECK  fast (default: size + mtime, hash only on mtime mismatch) | full (hash every file)
"""
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODES = ("internal_research", "redistribution")
DRIFT_CHECKS = ("fast", "full")


@dataclass(frozen=True)
class Settings:
    release: str | None = None
    mode: str = "internal_research"
    drift_check: str = "fast"

    def __post_init__(self):
        if self.mode not in MODES:
            raise ValueError(f"QUANTERA_API_MODE must be one of {MODES}, got {self.mode!r}")
        if self.drift_check not in DRIFT_CHECKS:
            raise ValueError(f"QUANTERA_DRIFT_CHECK must be one of {DRIFT_CHECKS}, got {self.drift_check!r}")

    @classmethod
    def from_env(cls):
        return cls(release=os.environ.get("QUANTERA_RELEASE") or None,
                   mode=os.environ.get("QUANTERA_API_MODE", "internal_research"),
                   drift_check=os.environ.get("QUANTERA_DRIFT_CHECK", "fast"))
