"""Application state: the pinned release, its datasets, a DuckDB connection and the registry."""
import os
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
        h = self.datasets["nse_security_id_history"]
        self.id_history = pd.read_parquet(h.files[0][1]) if h.files else None
        self.cache, self.cache_problem = cache.load(self.release, self.datasets)
        self.con = duckdb.connect(config={"threads": int(os.environ.get("QUANTERA_DUCKDB_THREADS", "4"))})
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

    def nse_security(self, instrument_id=None, symbol=None):
        """(security_key, segments) for an NSE instrument_id, or for a symbol that has ever
        traded on NSE (old symbols resolve to the security that carried them)."""
        h = self.id_history
        if h is None:
            return None, None
        if instrument_id:
            hit = h[(h.instrument_id == instrument_id) | (h.security_key == instrument_id)]
        else:
            hit = h[h.symbol == symbol.upper()].sort_values("last_date")
        if hit.empty:
            return None, None
        key = hit.security_key.iloc[-1]
        return key, h[h.security_key == key].sort_values("first_date")
