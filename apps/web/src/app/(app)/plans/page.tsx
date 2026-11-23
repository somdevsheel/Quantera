"use client";
import { useSearchParams } from "next/navigation";
import { Fragment, Suspense, useState } from "react";

import PriceLevels from "@/components/PriceLevels";
import { Badge, Card, ErrorBox, Field, FormError, KV, Load, Notes, Notice, PageHead, StockLink } from "@/components/ui";
import { api } from "@/lib/api";
import { date, dateTime, num } from "@/lib/fmt";
import { useAction, useApi } from "@/lib/hooks";
import type { Plan, Portfolio } from "@/lib/types";

type R = Record<string, any>;
const STOP = ["ATR", "PERCENT", "FIXED_PRICE", "SWING", "VOLATILITY"];
const TARGET = ["RISK_REWARD", "PERCENT", "ATR", "FIXED_PRICE"];
const TRAIL = ["ATR", "PERCENT", "AMOUNT"];

function Builder({ onCreated }: { onCreated: () => void }) {
  const params = useSearchParams();
  const pfs = useApi<Portfolio[]>("portfolios");
  const prev = useAction();
  const make = useAction();
  const [f, setF] = useState({
    symbol: params.get("symbol") ?? "", side: "LONG", entry_price: "",
    stop_method: "ATR", stop_value: "2", target_method: "RISK_REWARD", target_value: "2",
    trailing: true, trail_method: "ATR", trail_value: "3", min_rr: "",
    portfolio_id: "", quantity: "", book_entry: false, activate: false, as_of: "",
    stop_order: "STOP_MARKET", stop_limit_offset: "",
  });
  const [preview, setPreview] = useState<R>();
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setF({ ...f, [k]: e.target.type === "checkbox" ? (e.target as HTMLInputElement).checked : e.target.value });
  const base = () => ({
    symbol: f.symbol.trim().toUpperCase(), side: f.side, entry_price: f.entry_price ? Number(f.entry_price) : undefined,
    stop: { method: f.stop_method, value: Number(f.stop_value) },
    targets: [{ method: f.target_method, value: Number(f.target_value), fraction: 1 }],
    trailing: f.trailing ? { method: f.trail_method, value: Number(f.trail_value) } : null,
    min_reward_risk: f.min_rr ? Number(f.min_rr) : undefined,
  });
  return (
    <Card title="Plan a trade">
      <form className="form" onSubmit={async (e) => {
        e.preventDefault();
        const r = await prev.run(() => api<R>("trade-plans/preview", { body: base() }));
        if (r) setPreview(r);
      }}>
        <FormError error={prev.error} />
        <div className="form cols">
          <Field label="Symbol (NSE)"><input required value={f.symbol} onChange={set("symbol")} /></Field>
          <Field label="Side"><select value={f.side} onChange={set("side")}><option>LONG</option><option>SHORT</option></select></Field>
          <Field label="Entry price" hint="Blank = last close"><input type="number" step="any" min={0} value={f.entry_price} onChange={set("entry_price")} /></Field>
          <Field label="Stop method"><select value={f.stop_method} onChange={set("stop_method")}>{STOP.map((s) => <option key={s}>{s}</option>)}</select></Field>
          <Field label="Stop value" hint={f.stop_method === "PERCENT" ? "fraction, e.g. 0.05" : f.stop_method === "FIXED_PRICE" ? "price" : "multiple or lookback"}>
            <input type="number" step="any" min={0} required value={f.stop_value} onChange={set("stop_value")} /></Field>
          <Field label="Target method"><select value={f.target_method} onChange={set("target_method")}>{TARGET.map((s) => <option key={s}>{s}</option>)}</select></Field>
          <Field label="Target value"><input type="number" step="any" min={0} required value={f.target_value} onChange={set("target_value")} /></Field>
          <Field label="Min reward/risk"><input type="number" step="any" min={0} value={f.min_rr} onChange={set("min_rr")} /></Field>
        </div>
        <div className="row gap-lg">
          <label className="check"><input type="checkbox" checked={f.trailing} onChange={set("trailing")} /> Trailing stop</label>
          {f.trailing && (
            <>
              <select value={f.trail_method} onChange={set("trail_method")} aria-label="Trailing method">{TRAIL.map((s) => <option key={s}>{s}</option>)}</select>
              <input type="number" step="any" min={0} style={{ width: 90 }} value={f.trail_value} onChange={set("trail_value")} aria-label="Trailing value" />
            </>
          )}
        </div>
        <button className="btn">Preview levels</button>
      </form>
      {preview && (
        <div className="stack" style={{ marginTop: 14 }}>
          <PriceLevels pl={preview.price_levels} />
          <KV items={[
            ["Stop basis", `${preview.explain?.stop_loss?.method ?? "—"}${preview.explain?.stop_loss?.atr ? ` (ATR ${num(preview.explain.stop_loss.atr)} × ${preview.explain.stop_loss.multiple})` : ""}`],
            ["Reference", `₹${num(preview.explain?.reference_price)} on ${date(preview.explain?.as_of)}`],
            ["Tick size", preview.explain?.tick_size ?? "—"],
            ["Reward / risk", num(preview.explain?.targets?.[0]?.reward_risk)],
          ]} />
          <Notes notes={preview.warnings} />
          <form className="form" onSubmit={async (e) => {
            e.preventDefault();
            const r = await make.run(() => api<Plan>("trade-plans", { body: {
              ...base(), portfolio_id: f.portfolio_id || undefined,
              quantity: f.quantity ? Number(f.quantity) : undefined, book_entry: f.book_entry, activate: f.activate,
              as_of: f.as_of || undefined, stop_order: f.stop_order,
              stop_limit_offset: f.stop_limit_offset ? Number(f.stop_limit_offset) : undefined } }));
            if (r) { setPreview(undefined); onCreated(); }
          }}>
            <FormError error={make.error} />
            <div className="form cols">
              <Field label="Paper portfolio">
                <select value={f.portfolio_id} onChange={set("portfolio_id")}>
                  <option value="">None (levels only)</option>
                  {pfs.data?.filter((p) => p.is_paper).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
              </Field>
              <Field label="Quantity"><input type="number" min={0} step="any" value={f.quantity} onChange={set("quantity")} /></Field>
              <Field label="Stop order"><select value={f.stop_order} onChange={set("stop_order")}><option>STOP_MARKET</option><option>STOP_LIMIT</option></select></Field>
              {f.stop_order === "STOP_LIMIT" && (
                <Field label="Limit offset (₹)"><input type="number" min={0} step="any" required value={f.stop_limit_offset} onChange={set("stop_limit_offset")} /></Field>
              )}
              <Field label="Replay from" hint="Past date = historical paper replay"><input type="date" value={f.as_of} onChange={set("as_of")} /></Field>
            </div>
            <div className="row gap-lg">
              <label className="check"><input type="checkbox" checked={f.book_entry} onChange={set("book_entry")} disabled={!f.portfolio_id || f.side === "SHORT"} /> Book the entry in the paper portfolio</label>
              <label className="check"><input type="checkbox" checked={f.activate} onChange={set("activate")} /> Activate now</label>
            </div>
            <button className="btn primary" disabled={make.busy}>Save plan</button>
          </form>
        </div>
      )}
    </Card>
  );
}

function PlanRow({ plan, onChange }: { plan: Plan; onChange: () => void }) {
  const [open, setOpen] = useState(false);
  const detail = useApi<Plan & R>(open ? `trade-plans/${plan.id}` : null);
  const act = useAction();
  const go = (a: string) => act.run(async () => {
    await api(`trade-plans/${plan.id}/${a}`, { body: { expected_version: plan.version } });
    onChange(); if (open) detail.reload();
  });
  const s = plan.status;
  const pl = plan.price_levels;
  return (
    <Fragment>
      <tr>
        <td><StockLink route={plan.route} symbol={plan.symbol} /></td><td><Badge value={plan.side} /></td><td><Badge value={s} /></td>
        <td className="num">{num(pl.current_price)}</td><td className="num">{num(pl.stop_loss.price)}</td>
        <td className="num">{num(pl.target_price.price)}</td><td className="num">{num(pl.trailing_stop_loss.price)}</td>
        <td>{date(plan.evaluated_through)}</td>
        <td><div className="row gap">
          {s === "DRAFT" && <button className="btn small" disabled={act.busy} onClick={() => go("activate")}>Activate</button>}
          {s === "ACTIVE" && <button className="btn small" disabled={act.busy} onClick={() => go("evaluate")}>Evaluate</button>}
          {s === "ACTIVE" && <button className="btn small" disabled={act.busy} onClick={() => go("pause")}>Pause</button>}
          {s === "PAUSED" && <button className="btn small" disabled={act.busy} onClick={() => go("resume")}>Resume</button>}
          {["DRAFT", "ACTIVE", "PAUSED"].includes(s) && (
            <button className="btn small danger" disabled={act.busy} onClick={() => { if (confirm("Cancel this plan?")) void go("cancel"); }}>Cancel</button>
          )}
          <button className="btn small" onClick={() => setOpen(!open)}>{open ? "Hide" : "Details"}</button>
        </div></td>
      </tr>
      {(open || act.error) && (
        <tr><td colSpan={9} className="wrap">
          <FormError error={act.error} />
          {open && (
            <Load q={detail}>
              {(d) => (
                <div className="stack">
                  <Notice>{d.disclaimer}</Notice>
                  <KV items={[
                    ["Position", `${num(d.position?.remaining, 0)} of ${num(d.position?.quantity, 0)} @ ₹${num(d.position?.entry_price)}`],
                    ["Entry date", date(d.position?.entry_date)], ["Books paper", d.position?.books_paper ? "Yes" : "No"],
                    ["Stop order", d.legs?.stop?.order ?? "—"], ["Engine", d.engine_version ?? "—"], ["Version", d.version],
                  ]} />
                  <Notes notes={d.warnings} />
                  <h3>Event log</h3>
                  <div className="table-wrap"><table>
                    <thead><tr><th>#</th><th>Event</th><th>Rule</th><th>Bar</th><th className="num">Price</th><th className="num">Qty</th><th>At</th></tr></thead>
                    <tbody>{(d.events ?? []).map((e: R) => (
                      <tr key={e.seq}><td>{e.seq}</td><td><Badge value={e.event} /></td><td className="small">{e.rule ?? "—"}</td><td>{date(e.bar_date)}</td>
                        <td className="num">{num(e.price)}</td><td className="num">{e.quantity == null ? "—" : num(e.quantity, 0)}</td>
                        <td className="muted small">{dateTime(e.at)}</td></tr>))}
                    </tbody>
                  </table></div>
                </div>
              )}
            </Load>
          )}
        </td></tr>
      )}
    </Fragment>
  );
}

function PlansInner() {
  const plans = useApi<Plan[]>("trade-plans");
  const act = useAction();
  const [ev, setEv] = useState<R>();
  return (
    <>
      <PageHead title="Trade plans" sub="Stop-loss, target and trailing-stop plans evaluated on daily bars. Paper only."
                actions={<button className="btn" disabled={act.busy} onClick={() => act.run(async () => {
                  setEv(await api<R>("trade-plans/evaluate", { method: "POST" })); plans.reload(); })}>Evaluate all active</button>} />
      {act.error && <ErrorBox error={act.error} />}
      {ev && <Notice tone="good">Evaluated: {Object.entries(ev).map(([k, v]) => `${k} ${v}`).join(" · ")}</Notice>}
      <Builder onCreated={plans.reload} />
      <div style={{ height: 14 }} />
      <Card title="Plans">
        <Load q={plans} empty={(d) => d.length === 0 && "No plans yet."}>
          {(d) => (
            <div className="table-wrap"><table>
              <thead><tr><th>Stock</th><th>Side</th><th>Status</th><th className="num">Current</th><th className="num">Stop</th>
                <th className="num">Target</th><th className="num">Trailing</th><th>Evaluated to</th><th /></tr></thead>
              <tbody>{d.map((p) => <PlanRow key={p.id} plan={p} onChange={plans.reload} />)}</tbody>
            </table></div>
          )}
        </Load>
      </Card>
    </>
  );
}

export default function PlansPage() {
  return <Suspense><PlansInner /></Suspense>;
}
