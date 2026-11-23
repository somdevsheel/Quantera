"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { HBars } from "@/components/charts";
import { Badge, Card, ErrorBox, Field, FormError, KV, Load, Notes, Notice, PageHead, StockLink, Tabs } from "@/components/ui";
import { api } from "@/lib/api";
import { date, inr, inrShort, num, pct, signClass } from "@/lib/fmt";
import { useAction, useApi } from "@/lib/hooks";
import type { Holdings, Portfolio, Tx } from "@/lib/types";

type R = Record<string, any>;
const TYPES = ["BUY", "SELL", "DEPOSIT", "WITHDRAWAL", "DIVIDEND", "FEE"] as const;
const TABS = ["Holdings", "Risk", "Review", "Ledger", "Add transactions"] as const;
type Tab = (typeof TABS)[number];

function HoldingsView({ h }: { h: Holdings }) {
  return (
    <div className="stack">
      <div className="grid g4">
        <div className="card stat"><span>Net asset value</span><b>{inr(h.nav)}</b><span>as of {date(h.as_of)}</span></div>
        <div className="card stat"><span>Invested</span><b>{inr(h.invested)}</b><span>Cash {inr(h.cash)}</span></div>
        <div className="card stat"><span>Realised P&L</span><b className={signClass(h.realized_pnl)}>{inr(h.realized_pnl)}</b><span>Fees {inr(h.fees)}</span></div>
        <div className="card stat"><span>Net deposits</span><b>{inr(h.external_flows)}</b>
          <span className={signClass(h.nav - h.external_flows)}>Total P&L {inr(h.nav - h.external_flows)}</span></div>
      </div>
      {h.warnings.length > 0 && <Notice tone="warn"><Notes notes={h.warnings} /></Notice>}
      {h.positions.length === 0 ? <div className="state empty">No open positions.</div> : (
        <div className="table-wrap"><table>
          <thead><tr><th>Stock</th><th className="num">Qty</th><th className="num">Avg cost</th><th className="num">Price</th>
            <th className="num">Value</th><th className="num">Unrealised</th><th className="num">Realised</th><th className="num">Dividends</th><th className="num">Weight</th></tr></thead>
          <tbody>{h.positions.map((p) => (
            <tr key={p.instrument_id}>
              <td><StockLink route={p.route} symbol={p.symbol} /></td>
              <td className="num">{num(p.quantity, 0)}</td><td className="num">{num(p.average_cost)}</td>
              <td className="num">{p.price == null ? <Badge value="MISSING" /> : num(p.price)}<div className="muted small">{date(p.price_date)}</div></td>
              <td className="num">{inr(p.market_value)}</td>
              <td className={`num ${signClass(p.unrealized_pnl)}`}>{inr(p.unrealized_pnl)}</td>
              <td className={`num ${signClass(p.realized_pnl)}`}>{inr(p.realized_pnl)}</td>
              <td className="num">{inr(p.dividends)}</td><td className="num">{pct(p.weight)}</td>
            </tr>))}
          </tbody>
        </table></div>
      )}
      {h.unpriced.length > 0 && <Notice tone="warn">{h.unpriced.length} position(s) have no price in the lake and are excluded from NAV.</Notice>}
    </div>
  );
}

function RiskView({ id }: { id: string }) {
  const q = useApi<R>(`portfolios/${id}/risk`);
  return (
    <Load q={q}>
      {(r) => r.status && r.status !== "OK" ? <div className="state empty"><Badge value={r.status} /> {r.reason ?? "Not enough data for a risk report."}</div> : (
        <div className="stack">
          <KV items={[["Volatility (ann.)", pct(r.volatility)], ["VaR 95% 1d (hist.)", pct(r.var_95_1d_hist, 2)], ["ES 95% 1d (hist.)", pct(r.es_95_1d_hist, 2)],
                      ["VaR 95% 1d (param.)", pct(r.var_95_1d_param, 2)], ["Hypothetical max DD", pct(r.max_drawdown_hypothetical)], ["Beta", num(r.beta)],
                      ["Avg pairwise corr.", num(r.average_pairwise_correlation)], ["Cash weight", pct(r.cash_weight)],
                      ["Effective names", num(r.concentration?.effective_names, 1)], ["Largest", `${r.concentration?.largest ?? "—"} ${pct(r.concentration?.top1)}`],
                      ["Max days to exit", num(r.liquidity?.max_days, 2)]]} />
          <p className="muted small">Weights: {r.weights_basis}; {r.window_days}-day window against {r.benchmark}.</p>
          <div className="grid g2">
            <Card title="Risk contribution"><HBars data={Object.entries(r.risk_contribution ?? {}).map(([label, value]) => ({ label, value: value as number })).sort((a, b) => b.value - a.value)} /></Card>
            <Card title="Sector exposure"><HBars data={Object.entries(r.sector_exposure ?? {}).map(([label, value]) => ({ label, value: value as number })).sort((a, b) => b.value - a.value)} /></Card>
          </div>
          <Card title="Stress tests">
            <div className="table-wrap"><table><thead><tr><th>Scenario</th><th className="num">P&L</th><th>Method</th></tr></thead>
              <tbody>{(r.stress ?? []).map((s: R) => (
                <tr key={s.scenario}><td>{s.scenario}</td><td className={`num ${signClass(s.pnl_pct)}`}>{pct(s.pnl_pct, 1, true)}</td><td className="small muted wrap">{s.note}</td></tr>))}</tbody></table></div>
          </Card>
        </div>
      )}
    </Load>
  );
}

function ReviewView({ id }: { id: string }) {
  const q = useApi<R>(`portfolios/${id}/review`);
  const act = useAction();
  return (
    <Load q={q}>
      {(r) => (
        <div className="stack">
          <div className="row gap">
            <button className="btn primary" disabled={act.busy} onClick={() => act.run(async () => {
              const j = await api<{ id: string }>(`portfolios/${id}/review/scan`, { method: "POST" });
              location.assign(`/scanner/${j.id}`);
            })}>Scan holdings now</button>
            <span className="muted small">Ranks holdings against NIFTY 500 and assigns review states.</span>
          </div>
          <FormError error={act.error} />
          {(r.rows ?? []).length === 0 ? <div className="state empty">No review yet.</div> : (
            <div className="table-wrap"><table>
              <thead><tr><th>Stock</th><th className="num">Weight</th><th>Holding state</th><th>Reason</th><th>Decision</th><th>As of</th></tr></thead>
              <tbody>{r.rows.map((x: R) => (
                <tr key={x.symbol}><td><StockLink route={x.route} symbol={x.symbol} /></td><td className="num">{pct(x.weight)}</td>
                  <td><Badge value={x.holding_state} /></td><td className="wrap small">{x.reason}</td>
                  <td>{x.decision ? <Badge value={x.decision.state} /> : "—"}</td><td>{date(x.decision?.as_of)}</td></tr>))}</tbody>
            </table></div>
          )}
          <Notes notes={r.notes} />
        </div>
      )}
    </Load>
  );
}

function Ledger({ id, onChange }: { id: string; onChange: () => void }) {
  const q = useApi<Tx[]>(`portfolios/${id}/transactions`);
  const act = useAction();
  return (
    <Load q={q} empty={(d) => d.length === 0 && "No transactions."}>
      {(d) => (
        <>
          <FormError error={act.error} />
          <div className="table-wrap"><table>
            <thead><tr><th>#</th><th>Date</th><th>Type</th><th>Stock</th><th className="num">Qty</th><th className="num">Price</th>
              <th className="num">Fees</th><th className="num">Amount</th><th>Source</th><th /></tr></thead>
            <tbody>{[...d].reverse().map((t) => (
              <tr key={t.id} style={t.voided ? { opacity: 0.5, textDecoration: "line-through" } : undefined}>
                <td className="num">{t.seq}</td><td>{date(t.trade_date)}</td><td><Badge value={t.type} /></td>
                <td>{t.symbol ? <StockLink symbol={t.symbol} /> : "—"}</td>
                <td className="num">{t.quantity ? num(t.quantity, 0) : "—"}</td><td className="num">{t.price ? num(t.price) : "—"}</td>
                <td className="num">{t.fees ? num(t.fees) : "—"}</td><td className="num">{t.amount ? inr(t.amount) : "—"}</td>
                <td className="muted small">{t.source}{t.voided && ` · voided: ${t.void_reason}`}</td>
                <td>{!t.voided && <button className="btn small" disabled={act.busy} onClick={() => {
                  const reason = prompt("Reason for voiding this entry (kept in the ledger):");
                  if (reason && reason.trim().length >= 3) void act.run(async () => {
                    await api(`portfolios/${id}/transactions/${t.id}/void`, { body: { reason } }); q.reload(); onChange(); });
                }}>Void</button>}</td>
              </tr>))}
            </tbody>
          </table></div>
        </>
      )}
    </Load>
  );
}

function AddTx({ id, onDone }: { id: string; onDone: () => void }) {
  const act = useAction();
  const blank = { trade_date: new Date().toISOString().slice(0, 10), type: "BUY", symbol: "", quantity: "", price: "", fees: "", amount: "", notes: "" };
  const [f, setF] = useState(blank);
  const [file, setFile] = useState<File | null>(null);
  const [csvDate, setCsvDate] = useState(blank.trade_date);
  const trade = f.type === "BUY" || f.type === "SELL";
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  return (
    <div className="grid g2">
      <Card title="Add a transaction">
        <form className="form" onSubmit={async (e) => {
          e.preventDefault();
          const n = (v: string) => (v === "" ? 0 : Number(v));
          const body = [{ trade_date: f.trade_date, type: f.type, symbol: trade || f.type === "DIVIDEND" ? f.symbol : undefined,
            quantity: n(f.quantity), price: n(f.price), fees: n(f.fees), amount: n(f.amount), notes: f.notes || undefined }];
          if (await act.run(() => api(`portfolios/${id}/transactions`, { body }))) { setF(blank); onDone(); }
        }}>
          <FormError error={act.error} />
          <div className="form cols">
            <Field label="Date"><input type="date" required value={f.trade_date} onChange={set("trade_date")} /></Field>
            <Field label="Type"><select value={f.type} onChange={set("type")}>{TYPES.map((t) => <option key={t}>{t}</option>)}</select></Field>
            {(trade || f.type === "DIVIDEND") && <Field label="Symbol (NSE)"><input required value={f.symbol} onChange={set("symbol")} /></Field>}
            {trade && <Field label="Quantity"><input type="number" min={0} step="any" required value={f.quantity} onChange={set("quantity")} /></Field>}
            {trade && <Field label="Price (₹)"><input type="number" min={0} step="any" required value={f.price} onChange={set("price")} /></Field>}
            {trade && <Field label="Fees (₹)"><input type="number" min={0} step="any" value={f.fees} onChange={set("fees")} /></Field>}
            {!trade && <Field label="Amount (₹)"><input type="number" min={0} step="any" required value={f.amount} onChange={set("amount")} /></Field>}
          </div>
          <Field label="Notes"><input maxLength={300} value={f.notes} onChange={set("notes")} /></Field>
          <button className="btn primary" disabled={act.busy}>Add</button>
        </form>
      </Card>
      <Card title="Import holdings from CSV">
        <form className="form" onSubmit={async (e) => {
          e.preventDefault();
          if (!file) return;
          const form = new FormData(); form.append("file", file); form.append("trade_date", csvDate);
          if (await act.run(() => api(`portfolios/${id}/holdings/import`, { form }))) { setFile(null); onDone(); }
        }}>
          <p className="small">Columns: <code>symbol,quantity,average_cost[,exchange]</code>. Each row becomes a BUY on the date below. Formulas are rejected.</p>
          <Field label="As of date"><input type="date" required value={csvDate} onChange={(e) => setCsvDate(e.target.value)} /></Field>
          <Field label="CSV file (max 5 MB)"><input type="file" accept=".csv,text/csv" required onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></Field>
          <button className="btn" disabled={act.busy || !file}>Import</button>
        </form>
      </Card>
    </div>
  );
}

export default function PortfolioPage() {
  const { id } = useParams<{ id: string }>();
  const pf = useApi<Portfolio>(`portfolios/${id}`);
  const h = useApi<Holdings>(`portfolios/${id}/holdings`);
  const [tab, setTab] = useState<Tab>("Holdings");
  const act = useAction();
  return (
    <Load q={pf}>
      {(p) => (
        <>
          <PageHead title={<>{p.name} {p.is_paper && <Badge value="PAPER" />}</>} sub={`Base currency ${p.base_currency} · max position ${pct(p.max_position_weight, 0)}`}
                    actions={<>
                      {p.is_paper && <Link className="btn" href={`/paper/${p.id}`}>Paper orders</Link>}
                      <button className="btn danger" disabled={act.busy} onClick={() => {
                        if (confirm(`Delete portfolio "${p.name}" and its ledger? This cannot be undone.`))
                          void act.run(async () => { await api(`portfolios/${id}`, { method: "DELETE" }); location.assign("/portfolios"); });
                      }}>Delete</button>
                    </>} />
          {act.error && <ErrorBox error={act.error} />}
          <Tabs tabs={TABS} value={tab} onChange={setTab} />
          {tab === "Holdings" && <Load q={h}>{(x) => <HoldingsView h={x} />}</Load>}
          {tab === "Risk" && <RiskView id={id} />}
          {tab === "Review" && <ReviewView id={id} />}
          {tab === "Ledger" && <Ledger id={id} onChange={h.reload} />}
          {tab === "Add transactions" && <AddTx id={id} onDone={() => { h.reload(); setTab("Holdings"); }} />}
          {tab === "Holdings" && h.data && <p className="muted small">Total value {inrShort(h.data.nav)}; prices from the lake close of each position&apos;s last bar.</p>}
        </>
      )}
    </Load>
  );
}
