"""FRED series (India + global). Values are the latest revision at retrieval: no vintages yet,
so these are not point-in-time (ALFRED vintages are planned)."""
from datetime import date

from fastapi import APIRouter, Depends

from ..deps import get_lake
from ..query import Meta, Page, fetch, page_params, respond, src

router = APIRouter(tags=["macro"])
NOTE = "latest revision at retrieval (retrieved_utc); not point-in-time - no vintages in this release"
DEDUP = "QUALIFY row_number() OVER (PARTITION BY series_id, observation_date ORDER BY retrieved_utc DESC, dataset) = 1"


@router.get("/v1/macro/series/list")
def series_list(page: Page = Depends(page_params), lake=Depends(get_lake)):
    d = lake.dataset("fred_series")
    sql = f"""SELECT series_id, any_value(title) AS title, any_value(units) AS units, any_value(frequency) AS frequency,
                     any_value(original_source) AS original_source, string_agg(DISTINCT dataset, ',') AS datasets,
                     min(CAST(observation_date AS DATE)) AS first_date, max(CAST(observation_date AS DATE)) AS last_date,
                     count(*) AS observations
              FROM {src()} GROUP BY series_id"""
    return respond(fetch(lake, sql, {"files": [p for _, p in d.files]}, "series_id", page), page, Meta.of(lake, d))


@router.get("/v1/macro/series")
def series(series_id: str, start: date | None = None, end: date | None = None,
           page: Page = Depends(page_params), lake=Depends(get_lake)):
    d = lake.dataset("fred_series")
    sql = f"""SELECT CAST(observation_date AS DATE) AS observation_date, series_id, value, title, units, frequency,
                     original_source, dataset, retrieved_utc, source_file
              FROM {src()} WHERE series_id = $sid AND CAST(observation_date AS DATE) BETWEEN $start AND $end {DEDUP}"""
    params = {"files": [p for _, p in d.files], "sid": series_id, "start": start or date(1900, 1, 1),
              "end": end or date(2100, 1, 1)}
    return respond(fetch(lake, sql, params, "observation_date", page), page, Meta.of(lake, d, notes=[NOTE]))
