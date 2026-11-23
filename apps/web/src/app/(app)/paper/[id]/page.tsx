"use client";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Fragment, Suspense, useState } from "react";

import { Badge, Card, Field, FormError, KV, Load, Notice, PageHead, StockLink } from "@/components/ui";
import { api } from "@/lib/api";
import { date, dateTime, inr, num, pct } from "@/lib/fmt";
import { useAction, useApi } from "@/lib/hooks";
import type { PaperOrder, Portfolio } from "@/lib/types";

type R = Record<string, any>;
const newId = () => (typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID().slice(0, 18) : String(Date.now()));

function Ticket({ id, onPlaced }: { id: string; onPlaced: () => void }) {
  const params = useSearchParams();
  const act = useAction();
  const [f, setF] = useState({ symbol: params.get("symbol") ?? "", side: "BUY", order_type: "MARKET", quantity: "",
                               limit_price: "", stop_price: "", tif: "DAY", placed_on: "" });
  const [cid, setCid] = useState(newId);
  const [last, setLast] = useState<PaperOrder>();
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  const needLimit = f.order_type === "LIMIT" || f.order_type === "STOP_LIMIT";
  const needStop = f.order_type === "STOP" || f.order_type === "STOP_LIMIT";
  return (
    <Card title="Order ticket (paper)">
      <form className="form" onSubmit={async (e) => {
        e.preventDefault();
        const o = await act.run(() => api<PaperOrder>(`paper-portfolios/${id}/orders`, { body: {
          client_order_id: cid, symbol: f.symbol.trim().toUpperCase(), side: f.side, order_type: f.order_type,
          quantity: Number(f.quantity), limit_price: needLimit ? Number(f.limit_price) : undefined,
          stop_price: needStop ? Number(f.stop_price) : undefined, tif: f.tif, placed_on: f.placed_on || undefined } }));
        if (o) { setLast(o); setCid(newId()); onPlaced(); }
      }}>
        <FormError error={act.error} />
        <div className="form cols">
          <Field label="Symbol (NSE)"><input required value={f.symbol} onChange={set("symbol")} /></Field>
          <Field label="Side"><select value={f.side} onChange={set("side")}><option>BUY</option><option>SELL</option></select></Field>
          <Field label="Type"><select value={f.order_type} onChange={set("order_type")}>{["MARKET", "LIMIT", "STOP", "STOP_LIMIT"].map((t) => <option key={t}>{t}</option>)}</select></Field>
          <Field label="Quantity"><input type="number" min={1} step={1} required value={f.quantity} onChange={set("quantity")} /></Field>
          {needLimit && <Field label="Limit price"><input type="number" min={0} step="any" required value={f.limit_price} onChange={set("limit_price")} /></Field>}
          {needStop && <Field label="Stop price"><input type="number" min={0} step="any" required value={f.stop_price} onChange={set("stop_price")} /></Field>}
          <Field label="Time in force"><select value={f.tif} onChange={set("tif")}><option value="DAY">DAY</option><option value="GTC">GTC (90 sessions)</option></select></Field>
          <Field label="Placed on" hint="Blank = latest market day; a past date replays history"><input type="date" value={f.placed_on} onChange={set("placed_on")} /></Field>
        </div>
        <button className="btn primary" disabled={act.busy}>Place paper order</button>
        <p className="muted small">Order id <code>{cid}</code> makes retries safe: resending the same order never duplicates it.</p>
      </form>
      {last && (last.status === "REJECTED"
        ? <Notice tone="bad">Rejected by pre-trade checks: {last.reason}</Notice>
        : <Notice tone="good">{last.side} {num(last.quantity, 0)} {last.symbol} accepted ({last.status}). Run “Simulate fills” to process the next sessions.</Notice>)}
    </Card>
  );
}

function Policy({ id }: { id: string }) {
  const act = useAction();
  const [f, setF] = useState({ daily_loss_limit: "", max_drawdown: "", max_position_weight: "" });
  const [st, setSt] = useState<R>();
  const [note, setNote] = useState("");
  const n = (v: string) => (v ? Number(v) / 100 : undefined);
  return (
    <Card title="Risk policy">
      <FormError error={act.error} />
      <form className="form" onSubmit={async (e) => {
        e.preventDefault();
        const r = await act.run(() => api<R>(`paper-portfolios/${id}/risk-policy`, { method: "PUT", body: {
          daily_loss_limit: n(f.daily_loss_limit), max_drawdown: n(f.max_drawdown), max_position_weight: n(f.max_position_weight) } }));
        if (r) setSt(r);
      }}>
        <div className="form cols">
          <Field label="Daily loss limit (%)"><input type="number" min={0.1} max={100} step="any" value={f.daily_loss_limit} onChange={(e) => setF({ ...f, daily_loss_limit: e.target.value })} /></Field>
          <Field label="Max drawdown (%)"><input type="number" min={0.1} max={100} step="any" value={f.max_drawdown} onChange={(e) => setF({ ...f, max_drawdown: e.target.value })} /></Field>
          <Field label="Max position weight (%)"><input type="number" min={0.1} max={100} step="any" value={f.max_position_weight} onChange={(e) => setF({ ...f, max_position_weight: e.target.value })} /></Field>
        </div>
        <div className="row gap">
          <button className="btn">Save policy</button>
          <button type="button" className="btn" disabled={act.busy} onClick={() => act.run(async () => setSt(await api<R>(`paper-portfolios/${id}/risk-policy/check`, { method: "POST" })))}>Check now</button>
        </div>
      </form>
      {st && (
        <div className="stack" style={{ marginTop: 12 }}>
          <KV items={[["State", <Badge key="s" value={st.state} />], ["NAV", inr(st.nav)], ["Drawdown", pct(st.drawdown)], ["Reason", st.state_reason ?? "—"]]} />
          {(st.breaches ?? []).length > 0 && (
            <div className="table-wrap"><table><thead><tr><th>Breach</th><th className="num">Value</th><th className="num">Limit</th><th>Action</th></tr></thead>
              <tbody>{st.breaches.map((b: R, i: number) => <tr key={i}><td>{b.kind}</td><td className="num">{pct(b.value)}</td><td className="num">{pct(b.limit)}</td><td>{b.action}</td></tr>)}</tbody></table></div>
          )}
          {st.state === "RISK_OFF" && (
            <div className="row gap">
              <input placeholder="Reset note (who reviewed, why)" value={note} onChange={(e) => setNote(e.target.value)} style={{ minWidth: 260 }} />
              <button className="btn danger" disabled={act.busy || note.length < 3} onClick={() => act.run(async () => setSt(await api<R>(`paper-portfolios/${id}/risk-policy/reset`, { body: { note } })))}>Reset to NORMAL</button>
            </div>
          )}
        </div>
      )}
      <p className="muted small">In RISK_OFF, new buys are rejected until a person resets the state with a note.</p>
    </Card>
  );
}

function Orders({ id, version }: { id: string; version: number }) {
  const q = useApi<PaperOrder[]>(`paper-portfolios/${id}/orders`, { query: { v: version } });
  const act = useAction();
  const [open, setOpen] = useState<string | null>(null);
  const detail = useApi<PaperOrder>(open ? `paper-portfolios/${id}/orders/${open}` : null);
  return (
    <Load q={q} empty={(d) => d.length === 0 && "No orders yet."}>
      {(d) => (
        <>
          <FormError error={act.error} />
          <div className="table-wrap"><table>
            <thead><tr><th>Placed</th><th>Stock</th><th>Side</th><th>Type</th><th className="num">Qty</th><th className="num">Filled</th><th className="num">Avg price</th>
              <th className="num">Fees</th><th>TIF</th><th>Status</th><th>Simulated to</th><th /></tr></thead>
            <tbody>{d.map((o) => (
              <Fragment key={o.id}>
                <tr>
                  <td>{date(o.placed_on)}</td><td><StockLink symbol={o.symbol} /></td><td><Badge value={o.side === "BUY" ? "LONG" : "SHORT"} title={o.side} /> {o.side}</td>
                  <td className="small">{o.order_type}{o.limit_price ? ` @ ${num(o.limit_price)}` : ""}{o.stop_price ? ` stop ${num(o.stop_price)}` : ""}</td>
                  <td className="num">{num(o.quantity, 0)}</td><td className="num">{num(o.filled_quantity, 0)}</td><td className="num">{num(o.average_price)}</td>
                  <td className="num">{num(o.fees)}</td><td>{o.tif}</td>
                  <td><Badge value={o.status} title={o.reason ?? undefined} />{o.reason && <div className="small muted">{o.reason}</div>}</td>
                  <td>{date(o.simulated_through)}</td>
                  <td><div className="row gap">
                    {o.status === "WORKING" && <button className="btn small" disabled={act.busy} onClick={() => act.run(async () => { await api(`paper-portfolios/${id}/orders/${o.id}/cancel`, { method: "POST" }); q.reload(); })}>Cancel</button>}
                    <button className="btn small" onClick={() => setOpen(open === o.id ? null : o.id)}>{open === o.id ? "Hide" : "Log"}</button>
                  </div></td>
                </tr>
                {open === o.id && (
                  <tr><td colSpan={12} className="wrap">
                    <Load q={detail}>{(x) => (
                      <div className="table-wrap"><table><thead><tr><th>Bar · event</th><th>From</th><th>To</th><th>Details</th></tr></thead>
                        <tbody>{(x.events ?? []).map((e, i) => (
                          <tr key={i}><td className="small">{date((e as R).bar_date)} · {(e as R).event}</td><td>{e.from ? <Badge value={e.from} /> : "—"}</td><td>{e.to ? <Badge value={e.to} /> : "—"}</td>
                            <td className="small mono wrap">{JSON.stringify((e as R).details ?? "")}</td></tr>))}</tbody></table></div>
                    )}</Load>
                  </td></tr>
                )}
              </Fragment>))}
            </tbody>
          </table></div>
        </>
      )}
    </Load>
  );
}

function Inner() {
  const { id } = useParams<{ id: string }>();
  const pf = useApi<Portfolio>(`portfolios/${id}`);
  const act = useAction();
  const [v, setV] = useState(0);
  const [msg, setMsg] = useState<R>();
  return (
    <Load q={pf}>
      {(p) => (
        <>
          <PageHead title={<>{p.name} <Badge value="PAPER" /></>} sub="Simulated orders only. Venue: PAPER (simulated)."
                    actions={<>
                      <button className="btn primary" disabled={act.busy} onClick={() => act.run(async () => { setMsg({ sim: await api<R>(`paper-portfolios/${id}/simulate`, { method: "POST" }) }); setV(v + 1); })}>Simulate fills</button>
                      <button className="btn" disabled={act.busy} onClick={() => act.run(async () => setMsg({ rec: await api<R>(`paper-portfolios/${id}/reconcile`) }))}>Reconcile</button>
                      <Link className="btn" href={`/portfolios/${id}`}>Holdings & risk</Link>
                    </>} />
          <FormError error={act.error} />
          {!p.is_paper && <Notice tone="bad">This is not a paper portfolio; orders are refused.</Notice>}
          {msg?.sim && <Notice tone="good">Simulation processed {msg.sim.fills} fill(s) on new sessions.</Notice>}
          {msg?.rec && (
            <Notice tone={msg.rec.status === "RECONCILED" ? "good" : "bad"}>
              Reconciliation: <b>{msg.rec.status}</b> — {msg.rec.orders} order(s), {msg.rec.ledger_fills} ledger fill(s){msg.rec.breaks?.length ? `; breaks: ${JSON.stringify(msg.rec.breaks)}` : ""}.
            </Notice>
          )}
          <div className="grid g2">
            <Ticket id={id} onPlaced={() => setV(v + 1)} />
            <Policy id={id} />
          </div>
          <div style={{ height: 14 }} />
          <Card title="Orders"><Orders id={id} version={v} /></Card>
        </>
      )}
    </Load>
  );
}

export default function PaperPortfolioPage() {
  return <Suspense><Inner /></Suspense>;
}
