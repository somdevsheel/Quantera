"""Release pinning: load a frozen manifest and check the lake still matches it.

A release (scripts/freeze_release.py) is a manifest, not a copy. The API only reads
files listed in the manifest, and refuses to start if any of them is missing or changed.
"""
import hashlib
import json

import pandas as pd

from .settings import ROOT

REL_DIR = ROOT / "data/releases"


def available():
    """All frozen releases, oldest first."""
    out = []
    if REL_DIR.exists():
        for d in REL_DIR.iterdir():
            meta = d / "release.json"
            if meta.exists() and (d / "release_manifest.parquet").exists():
                out.append(json.loads(meta.read_text()))
    return sorted(out, key=lambda m: m["frozen_utc"])


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Release:
    def __init__(self, tag=None):
        rels = available()
        if not rels:
            raise RuntimeError("no frozen release under data/releases (run scripts/freeze_release.py)")
        meta = next((m for m in rels if m["tag"] == tag), None) if tag else rels[-1]
        if meta is None:
            raise RuntimeError(f"release {tag!r} not found; available: {[m['tag'] for m in rels]}")
        self.tag = meta["tag"]
        self.meta = meta
        self.frozen_date = meta["frozen_utc"][:10]
        self.manifest = pd.read_parquet(REL_DIR / self.tag / "release_manifest.parquet",
                                        columns=["path", "bytes", "mtime", "sha256"])
        self.paths = self.manifest.path.tolist()

    def drift(self, mode="fast"):
        """Compare the lake with the manifest. fast: size + mtime, hashing only files whose
        mtime moved (so a touched-but-identical file is not drift). full: hash everything."""
        missing, changed = [], []
        for path, size, mtime, digest in self.manifest.itertuples(index=False, name=None):
            p = ROOT / path
            try:
                st = p.stat()
            except FileNotFoundError:
                missing.append(path)
                continue
            if st.st_size != size:
                changed.append(path)
            elif (mode == "full" or int(st.st_mtime) != mtime) and _sha256(p) != digest:
                changed.append(path)
        return {"release": self.tag, "mode": mode, "files": len(self.manifest),
                "missing": len(missing), "changed": len(changed),
                "examples": (missing[:10] + changed[:10])}
