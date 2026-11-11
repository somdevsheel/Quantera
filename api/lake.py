"""Application state: the pinned release, its datasets, a DuckDB connection and the registry."""
import sqlite3

import duckdb
import pandas as pd

from . import cache
from . import datasets as ds
from .errors import APIError
from .release import Release
from .settings import ROOT, Settings

REGISTRY_PATH = "data/registry/quantera_registry.db"


class Lake:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.release = Release(settings.release)
        self.drift = self.release.drift(settings.drift_check)
        if self.drift["missing"] or self.drift["changed"]:
            raise RuntimeError(
                f"RELEASE_DRIFT: lake differs from release {self.release.tag}: {self.drift['missing']} missing, "
                f"{self.drift['changed']} changed (e.g. {self.drift['examples'][:3]}). "
                f"Run scripts/freeze_release.py --verify {self.release.tag}")
        self.datasets = ds.load(self.release.paths)
        self.cache, self.cache_problem = cache.load(self.release, self.datasets)
        self.con = duckdb.connect(config={"threads": 8})
        self.registry = self._load_registry()

    def _load_registry(self):
        if REGISTRY_PATH not in set(self.release.paths):
            raise RuntimeError("registry is not part of the release manifest")
        con = sqlite3.connect(f"file:{ROOT / REGISTRY_PATH}?mode=ro&immutable=1", uri=True)
        try:
            return {t: pd.read_sql(f"SELECT * FROM {t}", con)
                    for t in ("exchange_listings", "instruments", "exchanges", "etf_metadata")}
        finally:
            con.close()

    def dataset(self, name):
        """Dataset handle; enforces the licensing mode (docs/api_design.md §5)."""
        d = self.datasets[name]
        if self.settings.mode == "redistribution" and not d.redistributable:
            raise APIError(403, "LICENCE_REQUIRED",
                           f"dataset {name} may not be redistributed in this deployment mode",
                           {"usage_rights": d.usage_rights})
        return d

    def cursor(self):
        return self.con.cursor()
