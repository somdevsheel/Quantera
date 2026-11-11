"""Identifier resolution against the release's registry snapshot.

Exchange data (NSE/BSE) is keyed by `exchange_listings` (instrument_id = "IN:<ISIN>",
BSE listing_code = scrip code). Global instruments (Yahoo-sourced) use the UUIDs in
`instruments`. For XNSE/XBOM, the exchange listing wins over the Yahoo instrument.
"""
from dataclasses import dataclass

from .errors import APIError, invalid, unknown_instrument

EXCHANGE_ALIASES = {"NSE": "XNSE", "BSE": "XBOM", "NASDAQ": "XNAS", "NYSE": "XNYS"}
EXCHANGE_DATA_MICS = {"XNSE", "XBOM"}


@dataclass(frozen=True)
class Listing:
    instrument_id: str
    exchange_mic: str
    symbol: str
    code: str | None
    name: str | None
    source: str          # exchange_listings | instruments

    def as_dict(self):
        return self.__dict__.copy()


def mic(exchange):
    return EXCHANGE_ALIASES.get(exchange.upper(), exchange.upper()) if exchange else None


def resolve(lake, instrument_id=None, symbol=None, exchange=None, bse_code=None, isin=None):
    if not any((instrument_id, symbol, bse_code, isin)):
        raise invalid("one of instrument_id, symbol (+exchange), bse_code or isin is required")
    el, ins = lake.registry["exchange_listings"], lake.registry["instruments"]
    ex = mic(exchange)
    if bse_code:
        el_hit, ins_hit = el[(el.exchange_mic == "XBOM") & (el.listing_code == str(bse_code))], ins.iloc[0:0]
    elif isin:
        el_hit = el[el.instrument_id == f"IN:{isin.upper()}"]
        ins_hit = ins[ins.isin == isin.upper()]
    elif instrument_id:
        el_hit, ins_hit = el[el.instrument_id == instrument_id], ins[ins.instrument_id == instrument_id]
    else:
        s = symbol.upper()
        el_hit, ins_hit = el[el.listing_symbol.str.upper() == s], ins[ins.canonical_symbol.str.upper() == s]
    if ex:
        el_hit, ins_hit = el_hit[el_hit.exchange_mic == ex], ins_hit[ins_hit.exchange_mic == ex]
    out = [Listing(r.instrument_id, r.exchange_mic, r.listing_symbol, r.listing_code, r.listing_name, "exchange_listings")
           for r in el_hit.itertuples()]
    covered = {l.exchange_mic for l in out}
    out += [Listing(r.instrument_id, r.exchange_mic, r.canonical_symbol, None, r.instrument_name, "instruments")
            for r in ins_hit.itertuples()
            if not (r.exchange_mic in EXCHANGE_DATA_MICS and r.exchange_mic in covered)]
    if not out:
        raise unknown_instrument(instrument_id=instrument_id, symbol=symbol, exchange=ex, bse_code=bse_code, isin=isin)
    return out


def resolve_one(lake, **kw):
    hits = resolve(lake, **kw)
    if len(hits) > 1:
        raise APIError(422, "AMBIGUOUS_IDENTIFIER", "identifier matches more than one listing; add exchange",
                       {"candidates": [h.as_dict() for h in hits]})
    return hits[0]
