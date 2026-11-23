"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo, useState } from "react";

import { LineChart, type Series } from "@/components/charts";
import { Badge, Card, ErrorBox, KV, Load, Notes, Notice, PageHead, Tabs } from "@/components/ui";
import { api, errorText } from "@/lib/api";
import { date, inr, inrShort, int, label, num, pct, score, signClass } from "@/lib/fmt";
import { useApi } from "@/lib/hooks";

type R = Record<string, any>;
const TABS = ["Overview", "Technical", "Volatility", "Statistics", "Fundamentals", "Valuation", "Events", "Risk", "Forecast", "Options"] as const;
type Tab = (typeof TABS)[number];
const PILLARS = ["MOMENTUM", "QUALITY", "GROWTH", "VALUATION", "RISK"];

function SectionState({ s, children }: { s: R | undefined; children: React.ReactNode }) {
  if (!s) return <div className="state empty">Not available.</div>;
  const st = s.status as string | undefined;
  if (st && !["OK", "PARTIAL", "STALE"].includes(st)) {
    return <div className="state empty"><Badge value={st} /> {s.reason ?? s.message ?? "This section has no data for this stock."}</div>;
  }
  return (
    <>
      {st === "STALE" && <Notice tone="warn">This section is <b>stale</b>{s.age_days ? ` (${s.age_days} days old)` : ""}; figures may no longer reflect the business.</Notice>}
      {st === "PARTIAL" && <Notice tone="warn">Partial data: some inputs are missing.</Notice>}
      {children}
      <Notes notes={s.notes} />
    </>
  );
}

function Actions({ sym, exchange, data }: { sym: string; exchange: string; data: R }) {
  const wls = useApi<{ id: string; name: string }[]>("watchlists");
  const [wl, setWl] = useState("");
  const [msg, setMsg] = useState<string>();
  const add = async () => {
    if (!wl) return;
    try { await api(`watchlists/${wl}/items`, { body: { symbol: sym, exchange: exchange === "XBOM" ? "BSE" : "NSE" } }); setMsg("Added to watchlist"); }
    catch (e) { setMsg(errorText(e)); }
  };
  const exportJson = () => {
    const blob = new Blob([JSON.stringify(data, null, 1)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = `${sym}_research_${data.as_of}.json`; a.click();
    URL.revokeObjectURL(a.href);
  };
  return (
    <div className="row gap">
      {wls.data && wls.data.length > 0 && (
        <>
          <select aria-label="Watchlist" value={wl} onChange={(e) => setWl(e.target.value)}>
            <option value="">Add to watchlist…</option>
            {wls.data.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
          </select>
          <button className="btn" disabled={!wl} onClick={add}>Add</button>
        </>
      )}
      <Link className="btn" href={`/alerts?symbol=${encodeURIComponent(sym)}`}>Create alert</Link>
      <Link className="btn" href={`/plans?symbol=${encodeURIComponent(sym)}`}>Trade plan</Link>
      <Link className="btn" href={`/paper?symbol=${encodeURIComponent(sym)}`}>Paper order</Link>
      <button className="btn" onClick={exportJson}>Export</button>
      {msg && <span className="small muted">{msg}</span>}
    </div>
  );
}

function Technical({ t }: { t: R }) {
  const [range, setRange] = useState(252);
  const bars: R[] = t.bars ?? [];
  const n = bars.length;
  const from = Math.max(0, n - range);
  const xs = bars.slice(from).map((b) => b.date as string);
  const ser = (name: string, key: string, color?: string, dashed = false): Series => ({
    name, color, dashed, points: xs.map((x, i) => ({ x, y: t.indicators?.[key]?.[from + i] ?? null })) });
  const price: Series = { name: "Close", points: bars.slice(from).map((b) => ({ x: b.date, y: b.close })) };
  const sig = t.signals ?? {};
  return (
    <div className="stack">
      <div className="row gap">{[63, 126, 252, 504].map((r) => (
        <button key={r} className={`btn small ${range === r ? "primary" : ""}`} onClick={() => setRange(r)}>{r === 63 ? "3M" : r === 126 ? "6M" : r === 252 ? "1Y" : "2Y"}</button>))}
        <span className="muted small">Prices adjusted for {t.adjustment ?? "splits"}.</span></div>
      <LineChart ariaLabel="Price with moving averages and Bollinger bands" height={300} yFormat={(v) => num(v, 0)}
                 series={[price, ser("SMA 50", "sma50", "var(--c2)"), ser("SMA 200", "sma200", "var(--c4)"),
                          ser("BB upper", "bb_upper", "var(--muted)", true), ser("BB lower", "bb_lower", "var(--muted)", true)]} />
      <div className="grid g2">
        <Card title="RSI (14)"><LineChart ariaLabel="RSI" height={150} yFormat={(v) => v.toFixed(0)} series={[ser("RSI 14", "rsi14")]}
                   hlines={[{ y: 70, label: "70" }, { y: 30, label: "30" }]} /></Card>
        <Card title="MACD (12, 26, 9)"><LineChart ariaLabel="MACD" height={150} series={[ser("MACD", "macd"), ser("Signal", "macd_signal", "var(--c2)")]} /></Card>
        <Card title="ADX (14)"><LineChart ariaLabel="ADX" height={150} yFormat={(v) => v.toFixed(0)} series={[ser("ADX", "adx14"), ser("+DI", "plus_di", "var(--c3)"), ser("−DI", "minus_di", "var(--c5)")]} /></Card>
        <Card title="Stochastic (14, 3)"><LineChart ariaLabel="Stochastic" height={150} yFormat={(v) => v.toFixed(0)} series={[ser("%K", "stoch_k"), ser("%D", "stoch_d", "var(--c2)")]}
                   hlines={[{ y: 80, label: "80" }, { y: 20, label: "20" }]} /></Card>
      </div>
      <Card title="Signals (latest bar)">
        <KV items={[
          ["Above SMA 50", sig.above_sma50 ? "Yes" : "No"], ["Above SMA 200", sig.above_sma200 ? "Yes" : "No"],
          ["Golden cross", sig.golden_cross ? "Yes" : "No"], ["RSI 14", num(sig.rsi14, 1)], ["ADX 14", num(sig.adx14, 1)],
          ["Trend strength", sig.trend_strength ?? "—"], ["20-day support", inr(sig.support_20d, 2)],
          ["20-day resistance", inr(sig.resistance_20d, 2)], ["52-week breakout", sig.breakout_52w ? "Yes" : "No"],
          ["% of 52-week high", pct(sig.pct_of_52w_high)], ["ATR 14", num(t.indicators?.atr14?.at(-1))],
        ]} />
      </Card>
    </div>
  );
}

function Volatility({ v, bars }: { v: R; bars: R[] }) {
  const est: R = v.estimators ?? {};
  const names = ["close_to_close", "parkinson", "garman_klass", "rogers_satchell", "yang_zhang"];
  const roll: number[] = v.rolling_21d ?? [];
  const xs = bars.slice(-roll.length).map((b) => b.date as string);
  return (
    <div className="stack">
      <div className="table-wrap"><table>
        <thead><tr><th>Estimator (annualised)</th>{Object.keys(est).map((w) => <th key={w} className="num">{w}</th>)}</tr></thead>
        <tbody>{names.map((n) => (
          <tr key={n}><td>{label(n)}</td>{Object.keys(est).map((w) => <td key={w} className="num">{pct(est[w]?.[n])}</td>)}</tr>))}
        </tbody>
      </table></div>
      {roll.length > 1 && xs.length === roll.length && (
        <Card title="Rolling 21-day volatility (close to close)">
          <LineChart ariaLabel="Rolling volatility" height={180} yFormat={(x) => `${(x * 100).toFixed(0)}%`}
                     series={[{ name: "21d vol", points: xs.map((x, i) => ({ x, y: roll[i] })) }]} />
        </Card>
      )}
    </div>
  );
}

function Fundamentals({ f }: { f: R }) {
  const qs: R[] = f.quarters ?? [];
  const m: R = f.metrics ?? {};
  return (
    <div className="stack">
      <KV items={[["Latest period", date(f.latest_period_end)], ["Filed", date(f.latest_filed)], ["Basis", f.basis ?? "—"],
                  ["Revenue growth (YoY)", <span key="r" className={signClass(m.REVENUE_GROWTH_YOY)}>{pct(m.REVENUE_GROWTH_YOY, 1, true)}</span>],
                  ["EPS growth (YoY)", <span key="e" className={signClass(m.EPS_GROWTH_YOY)}>{pct(m.EPS_GROWTH_YOY, 1, true)}</span>],
                  ["Net margin (TTM)", pct(m.NET_MARGIN_TTM)], ["EPS (TTM)", inr(m.EPS_TTM, 2)]]} />
      <div className="table-wrap"><table>
        <thead><tr><th>Quarter to</th><th>Basis</th><th>Audited</th><th className="num">Revenue</th><th className="num">Expenses</th>
          <th className="num">PBT</th><th className="num">Net profit</th><th className="num">EPS</th><th>Filed</th></tr></thead>
        <tbody>{qs.map((q, i) => (
          <tr key={i}><td>{date(q.to_date)}</td><td>{q.consolidated}</td><td>{q.audited}</td>
            <td className="num">{inrShort(q.revenue_from_operations)}</td><td className="num">{inrShort(q.total_expenses)}</td>
            <td className="num">{inrShort(q.profit_before_tax)}</td><td className="num">{inrShort(q.net_profit)}</td>
            <td className="num">{num(q.eps_basic)}</td><td className="muted">{date(q.filing_datetime)}</td></tr>))}
        </tbody>
      </table></div>
      <p className="muted small">Source: {f.source}</p>
    </div>
  );
}

function Quant({ q }: { q: R }) {
  return (
    <div className="stack">
      <KV items={[
        ["Window", q.window], ["Total return", pct(q.total_return, 1, true)], ["Volatility", pct(q.volatility)],
        ["Sharpe", num(q.sharpe)], ["Sortino", num(q.sortino)], ["Max drawdown", pct(q.max_drawdown)], ["Calmar", num(q.calmar)],
        ["Beta", num(q.beta)], ["Alpha (annual)", pct(q.alpha_annual, 1, true)], ["Correlation", num(q.correlation)],
        ["Tracking error", pct(q.tracking_error)], ["VaR 95% (1d)", pct(q.var_95_1d)], ["ES 95% (1d)", pct(q.es_95_1d)],
        ["Best day", pct(q.best_day, 1, true)], ["Worst day", pct(q.worst_day, 1, true)], ["Positive days", pct(q.positive_days)],
      ]} />
      <p className="muted small">Benchmark {q.benchmark}; {q.observations} daily observations.</p>
      {q.momentum && <Card title="Momentum"><KV items={Object.entries(q.momentum as R).map(([k, v]) => [label(k), <span key={k} className={signClass(v)}>{pct(v, 1, true)}</span>])} /></Card>}
    </div>
  );
}

export default function StockPage() {
  const { exchange, symbol } = useParams<{ exchange: string; symbol: string }>();
  const [asOf, setAsOf] = useState("");
  const q = useApi<R>(`stocks/${exchange}/${decodeURIComponent(symbol)}`, { query: { as_of: asOf || undefined } });
  const [tab, setTab] = useState<Tab>("Overview");
  const bars = useMemo(() => (q.data?.technical?.bars ?? []) as R[], [q.data]);

  return (
    <Load q={q}>
      {(d) => {
        const o: R = d.overview ?? {};
        const dec: R | undefined = o.decision;
        return (
          <>
            <PageHead title={<>{d.instrument.symbol} <span className="muted small">{d.instrument.exchange === "XNSE" ? "NSE" : "BSE"}</span></>}
                      sub={<>{d.instrument.name} · as of <b>{date(d.as_of)}</b> · lake <code>{d.lake_release}</code></>}
                      actions={<Actions sym={d.instrument.symbol} exchange={d.instrument.exchange} data={d} />} />
            {q.error && <ErrorBox error={q.error} />}
            <div className="grid g4">
              <div className="card stat"><span>Last close ({date(o.last_date)})</span><b>{inr(o.close, 2)}</b>
                <span className={signClass(o.change_pct)}>{num(o.change)} ({pct(o.change_pct, 2, true)})</span></div>
              <div className="card stat"><span>52-week range</span><b>{inr(o.low_52w, 0)} – {inr(o.high_52w, 0)}</b>
                <span>Volume {int(o.volume)}</span></div>
              <div className="card stat"><span>Avg traded value (20d)</span><b>{inrShort(o.adv_value_20d)}</b>
                <span>{o.freshness?.stale ? <Badge value="STALE" /> : <>Prices current to {date(o.freshness?.prices_last)}</>}</span></div>
              <div className="card stat"><span>Latest decision</span>
                {dec ? <><b><Badge value={dec.state} /></b><span>Opportunity {score(dec.opportunity)} · uncertainty {score(dec.uncertainty)}</span></>
                     : <><b className="muted">None</b><span>Not in any scan yet. <Link href="/scanner">Run a scan</Link>.</span></>}
              </div>
            </div>
            <div className="row gap" style={{ margin: "12px 0" }}>
              <label className="small muted">View as of</label>
              <input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} />
              {asOf && <button className="btn small" onClick={() => setAsOf("")}>Latest</button>}
              <span className="muted small">{d.disclaimer}</span>
            </div>
            <Tabs tabs={TABS} value={tab} onChange={setTab} />
            {tab === "Overview" && (
              <div className="stack">
                {bars.length > 0 && <LineChart ariaLabel="One-year price" height={260} yFormat={(v) => num(v, 0)}
                  series={[{ name: "Close", points: bars.slice(-252).map((b) => ({ x: b.date, y: b.close })) }]} />}
                {dec && (
                  <Card title="Decision explanation" actions={<span className="muted small">review by {date(dec.review_by)}</span>}>
                    <p>{dec.explanation}</p>
                    <div className="hbars">
                      {PILLARS.map((p) => (
                        <div key={p} className="hbar"><span className="lbl">{label(p)}</span>
                          <span className="track">{dec.pillars?.[p] != null && <span className="fill" style={{ width: `${dec.pillars[p]}%`, background: "var(--accent)" }} />}</span>
                          <span className="val">{dec.pillars?.[p] == null ? <span className="muted">n/a</span> : score(dec.pillars[p])}</span></div>
                      ))}
                    </div>
                    <p className="muted small">Pillars are percentiles within the scanned universe; n/a means the input is missing or stale and is not counted as neutral.</p>
                  </Card>
                )}
              </div>
            )}
            {tab === "Technical" && <SectionState s={d.technical}><Technical t={d.technical} /></SectionState>}
            {tab === "Volatility" && <SectionState s={d.volatility}><Volatility v={d.volatility} bars={bars} /></SectionState>}
            {tab === "Statistics" && <SectionState s={d.quant}><Quant q={d.quant} /></SectionState>}
            {tab === "Fundamentals" && <SectionState s={d.fundamentals}><Fundamentals f={d.fundamentals} /></SectionState>}
            {tab === "Valuation" && (
              <SectionState s={d.valuation}>
                <KV items={[["P/E (TTM)", d.valuation.loss_making ? "n/m (loss-making)" : num(d.valuation.pe_ttm, 1)], ["Earnings yield (TTM)", pct(d.valuation.earnings_yield_ttm)]]} />
                <h3>Not computed</h3>
                <ul className="notes">{Object.entries(d.valuation.unsupported ?? {}).map(([k, v]) => <li key={k}><b>{k.toUpperCase()}</b>: {String(v)}</li>)}</ul>
              </SectionState>
            )}
            {tab === "Events" && (
              <SectionState s={d.events}>
                <div className="grid g2">
                  <Card title="Corporate actions">
                    {(d.events.corporate_actions ?? []).length === 0 ? <div className="state empty">None recorded.</div> : (
                      <div className="table-wrap"><table><thead><tr><th>Ex-date</th><th>Event</th><th>Details</th></tr></thead>
                        <tbody>{[...d.events.corporate_actions].reverse().map((c: R, i: number) => (
                          <tr key={i}><td>{date(c.ex_date)}</td><td><Badge value={c.event} /></td><td className="wrap small">{c.subject}</td></tr>))}</tbody></table></div>
                    )}
                  </Card>
                  <Card title="Calendar">
                    {(d.events.calendar ?? []).length === 0 ? <div className="state empty">No events.</div> : (
                      <div className="table-wrap"><table><thead><tr><th>Date</th><th>Purpose</th></tr></thead>
                        <tbody>{[...d.events.calendar].reverse().map((c: R, i: number) => (
                          <tr key={i}><td>{date(c.event_date)} {c.is_future && <Badge value="UPCOMING" />}</td><td className="wrap small">{c.purpose}</td></tr>))}</tbody></table></div>
                    )}
                  </Card>
                </div>
              </SectionState>
            )}
            {tab === "Risk" && (
              <SectionState s={d.risk}>
                <KV items={[["VaR 95% (1d)", pct(d.risk.var_95_1d, 2)], ["ES 95% (1d)", pct(d.risk.es_95_1d, 2)], ["Max drawdown (1y)", pct(d.risk.max_drawdown_1y)],
                            ["Downside vol (1y)", pct(d.risk.downside_vol_1y)], ["Beta (1y)", num(d.risk.beta_1y)],
                            ["Avg traded value (20d)", inrShort(d.risk.liquidity?.adv_value_20d)], ["Zero-volume days (3m)", pct(d.risk.liquidity?.zero_volume_share_3m)]]} />
              </SectionState>
            )}
            {tab === "Forecast" && (
              <SectionState s={d.forecast}>
                <Notice>{d.forecast.note} ({d.forecast.classification})</Notice>
                <div className="table-wrap"><table>
                  <thead><tr><th>Horizon</th><th>As of</th><th className="num">Score</th><th className="num">Percentile</th>
                    <th className="num">90% interval</th><th>Meaning</th><th>Model</th></tr></thead>
                  <tbody>{(d.forecast.horizons ?? []).map((h: R) => (
                    <tr key={h.horizon_days}>
                      <td>{h.horizon_days} days</td><td>{date(h.as_of)}</td><td className="num">{num(h.score, 4)}</td>
                      <td className="num">{score(h.percentile)}</td>
                      <td className="num">{h.interval_90?.[0] == null ? "—" : `${num(h.interval_90[0], 3)} … ${num(h.interval_90[1], 3)}`}</td>
                      <td className="small">{h.meaning}</td>
                      <td className="small"><Link href="/models">{h.model?.name} v{h.model?.version}</Link> ({h.model?.type}, data to {date(h.model?.training_cutoff)})</td>
                    </tr>))}
                  </tbody>
                </table></div>
              </SectionState>
            )}
            {tab === "Options" && <SectionState s={d.options}>{null}</SectionState>}
          </>
        );
      }}
    </Load>
  );
}
