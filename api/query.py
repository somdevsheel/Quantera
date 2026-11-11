"""Query execution, cursor paging and response formatting (JSON or Arrow IPC)."""
import base64
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime

import pyarrow as pa
import pyarrow.compute as pc
from fastapi import Query, Response

from .errors import invalid, no_data

DEFAULT_LIMIT, MAX_LIMIT = 10_000, 100_000
SRC = "read_parquet({param}, union_by_name=true)"


def src(param="files"):
    return SRC.format(param="$" + param)


@dataclass
class Page:
    limit: int
    offset: int
    fmt: str


def page_params(limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
                cursor: str | None = Query(None, description="next_cursor from the previous page"),
                format: str = Query("json", pattern="^(json|arrow)$")) -> Page:
    offset = 0
    if cursor:
        try:
            offset = int(json.loads(base64.urlsafe_b64decode(cursor.encode()))["o"])
        except Exception:  # noqa: BLE001
            raise invalid("malformed cursor") from None
    return Page(limit, offset, format)


def _cursor(offset):
    return base64.urlsafe_b64encode(json.dumps({"o": offset}).encode()).decode()


@dataclass
class Meta:
    release: str
    datasets: list
    notes: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)
    lake: object = None

    @classmethod
    def of(cls, lake, *dsets, notes=None, **extra):
        return cls(lake.release.tag, list(dsets), list(notes or []), extra, lake)

    def body(self):
        out = {"release": self.release,
               "dataset": ",".join(d.name for d in self.datasets),
               "schema_version": ",".join(filter(None, (d.schema_version for d in self.datasets))) or None,
               "usage_rights": " | ".join(dict.fromkeys(d.usage_rights for d in self.datasets))}
        out.update(self.extra)
        if self.notes:
            out["notes"] = self.notes
        return out

    def headers(self):
        b = self.body()
        return {"X-Quantera-Release": b["release"], "X-Quantera-Dataset": b["dataset"],
                "X-Usage-Rights": b["usage_rights"].encode("ascii", "replace").decode()}


def used(sql, params):
    """DuckDB rejects bound parameters the SQL does not reference; keep only the used ones."""
    return {k: v for k, v in params.items() if re.search(rf"\${k}\b", sql)}


def fetch(lake, sql, params, order_by, page: Page):
    """Run `sql` with deterministic ordering and one page of rows (plus one to detect more)."""
    full = f"SELECT * FROM ({sql}) AS q ORDER BY {order_by} LIMIT {page.limit + 1} OFFSET {page.offset}"
    return lake.cursor().sql(full, params=used(full, params)).to_arrow_table()


def _clean(tbl: pa.Table) -> pa.Table:
    """NaN -> null in float columns, so JSON stays valid and consumers see missing values."""
    cols = []
    for f, col in zip(tbl.schema, tbl.columns):
        if pa.types.is_floating(f.type):
            col = pc.if_else(pc.is_nan(col), pa.scalar(None, f.type), col)
        cols.append(col)
    return pa.Table.from_arrays(cols, schema=tbl.schema)


def _json_rows(lake, tbl: pa.Table) -> str:
    """Serialise an Arrow page to a JSON array inside DuckDB (C++), ~10x faster than
    Python dicts for long histories. Row order is pinned by an explicit row number;
    NaN -> null; timestamps as ISO 8601 (YYYY-MM-DDTHH:MM:SS); dates as YYYY-MM-DD."""
    if tbl.num_rows == 0:
        return "[]"
    fields = []
    for f in tbl.schema:
        q = '"' + f.name.replace('"', '""') + '"'
        if pa.types.is_floating(f.type):
            expr = f"CASE WHEN isnan({q}) THEN NULL ELSE {q} END"
        elif pa.types.is_timestamp(f.type):
            expr = f"strftime({q}, '%Y-%m-%dT%H:%M:%S')"
        else:
            expr = q
        fields.append(f"{q} := {expr}")
    page = tbl.append_column("__rn", pa.array(range(tbl.num_rows), pa.int64()))
    cur = lake.cursor()
    cur.register("__page", page)
    try:
        sql = f"SELECT CAST(to_json(list(struct_pack({', '.join(fields)}) ORDER BY __rn)) AS VARCHAR) FROM __page"
        return cur.execute(sql).fetchall()[0][0]
    finally:
        cur.unregister("__page")


def _default(o):
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    return str(o)


def respond(tbl: pa.Table, page: Page, meta: Meta, empty_is_404=True):
    more = tbl.num_rows > page.limit
    tbl = tbl.slice(0, page.limit)
    if tbl.num_rows == 0 and page.offset == 0 and empty_is_404:
        raise no_data()
    nxt = _cursor(page.offset + page.limit) if more else None
    headers = meta.headers()
    if page.fmt == "arrow":
        sink = pa.BufferOutputStream()
        with pa.ipc.new_stream(sink, tbl.schema) as w:
            w.write_table(tbl)
        if nxt:
            headers["X-Next-Cursor"] = nxt
        return Response(sink.getvalue().to_pybytes(), media_type="application/vnd.apache.arrow.stream", headers=headers)
    head = meta.body() | {"count": tbl.num_rows, "next_cursor": nxt}
    if meta.lake is not None:
        data = _json_rows(meta.lake, tbl)
    else:
        data = json.dumps(_clean(tbl).to_pylist(), default=_default, allow_nan=False)
    body = json.dumps(head, default=_default, allow_nan=False)[:-1] + ', "data": ' + data + "}"
    return Response(body, media_type="application/json", headers=headers)


def respond_obj(obj, meta: Meta | None = None):
    headers = meta.headers() if meta else {}
    body = (meta.body() if meta else {}) | obj
    return Response(json.dumps(body, default=_default), media_type="application/json", headers=headers)


def ymd(d: date | None):
    return d.strftime("%Y%m%d") if d else None


def require_files(files, **details):
    if not files:
        raise no_data("no files in the release for the requested range", **details)
    return files
