"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { Badge, Card, Field, FormError, Load, PageHead } from "@/components/ui";
import { api } from "@/lib/api";
import { date, dateTime, num, pct, signClass } from "@/lib/fmt";
import { useAction, useApi } from "@/lib/hooks";
import type { Job } from "@/lib/types";

type Row = { id: string; status: string; created_at: string; family: string; signal: string; start: string; end: string;
  metrics: { cagr?: number; sharpe?: number; max_drawdown?: number } | null; deflated_sharpe: number | null; error: string | null };
type Library = { signals: string[] };

function Inner() {
  const router = useRouter();
  const params = useSearchParams();
  const lib = useApi<Library>("strategies");
  const list = useApi<Row[]>("backtests", { pollMs: 4000, keepPolling: (d) => d.some((r) => r.status === "QUEUED" || r.status === "RUNNING") });
  const act = useAction();
  const [f, setF] = useState({
    name: "Backtest", signal: params.get("signal") ?? "momentum_12_1", start: "2018-01-01", end: "2024-12-31",
    liquid_top: "100", rebalance: "M", selection: "TOP_QUANTILE", quantile: "0.2", top_n: "20", weighting: "EQUAL",
    max_weight: "10", capital: "10000000", brokerage_bps: "3", spread_bps: "10", impact_coef: "0.1", grid: "",
  });
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => setF({ ...f, [k]: e.target.value });

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    let grid: unknown[] = [];
    if (f.grid.trim()) {
      try { grid = JSON.parse(f.grid); } catch { act.setError("Walk-forward grid must be a JSON list, e.g. [{\"lookback\": 126}, {\"lookback\": 252}]"); return; }
    }
    const job = await act.run(() => api<Job>("backtests", { body: {
      name: f.name, signal: f.signal, start: f.start, end: f.end, universe: "LIQUID", liquid_top: Number(f.liquid_top),
      rebalance: f.rebalance, selection: f.selection, quantile: Number(f.quantile), top_n: Number(f.top_n), weighting: f.weighting,
      max_weight: Number(f.max_weight) / 100, capital: Number(f.capital), brokerage_bps: Number(f.brokerage_bps),
      spread_bps: Number(f.spread_bps), impact_coef: Number(f.impact_coef), grid } }));
    if (job) router.push(`/backtests/${job.id}`);
  };

  return (
    <>
      <PageHead title="Backtests" sub="Point-in-time liquid universe (delisted stocks included), next-session execution, itemised Indian costs, walk-forward and deflated Sharpe." />
      <div className="grid g2">
        <Card title="New backtest">
          <form className="form" onSubmit={submit}>
            <FormError error={act.error} />
            <div className="form cols">
              <Field label="Name"><input required maxLength={120} value={f.name} onChange={set("name")} /></Field>
              <Field label="Signal"><select value={f.signal} onChange={set("signal")}>{(lib.data?.signals ?? [f.signal]).map((s) => <option key={s}>{s}</option>)}</select></Field>
              <Field label="Start"><input type="date" required value={f.start} onChange={set("start")} /></Field>
              <Field label="End"><input type="date" required value={f.end} onChange={set("end")} /></Field>
              <Field label="Liquid universe (top N)"><input type="number" min={10} max={500} value={f.liquid_top} onChange={set("liquid_top")} /></Field>
              <Field label="Rebalance"><select value={f.rebalance} onChange={set("rebalance")}><option value="M">Monthly</option><option value="Q">Quarterly</option></select></Field>
              <Field label="Selection"><select value={f.selection} onChange={set("selection")}><option value="TOP_QUANTILE">Top quantile</option><option value="TOP_N">Top N</option></select></Field>
              {f.selection === "TOP_QUANTILE"
                ? <Field label="Quantile"><input type="number" step="0.05" min={0.05} max={0.5} value={f.quantile} onChange={set("quantile")} /></Field>
                : <Field label="Top N"><input type="number" min={1} max={200} value={f.top_n} onChange={set("top_n")} /></Field>}
              <Field label="Weighting"><select value={f.weighting} onChange={set("weighting")}><option value="EQUAL">Equal</option><option value="INVERSE_VOL">Inverse volatility</option></select></Field>
              <Field label="Max weight (%)"><input type="number" min={1} max={100} value={f.max_weight} onChange={set("max_weight")} /></Field>
              <Field label="Capital (₹)"><input type="number" min={1} value={f.capital} onChange={set("capital")} /></Field>
              <Field label="Brokerage (bps)"><input type="number" min={0} max={100} step="any" value={f.brokerage_bps} onChange={set("brokerage_bps")} /></Field>
              <Field label="Spread (bps)"><input type="number" min={0} max={500} step="any" value={f.spread_bps} onChange={set("spread_bps")} /></Field>
              <Field label="Impact coefficient"><input type="number" min={0} max={5} step="any" value={f.impact_coef} onChange={set("impact_coef")} /></Field>
            </div>
            <Field label="Walk-forward parameter sets (optional JSON list)" hint="Each set is tried on the training window; the best is tested on the next window. Every set counts as a trial.">
              <textarea rows={2} value={f.grid} onChange={set("grid")} placeholder='[{"lookback": 126}, {"lookback": 252}]' />
            </Field>
            <button className="btn primary" disabled={act.busy}>{act.busy ? "Queuing…" : "Run backtest"}</button>
          </form>
        </Card>
        <Card title="Runs">
          <Load q={list} empty={(d) => d.length === 0 && "No backtests yet."}>
            {(d) => (
              <div className="table-wrap"><table>
                <thead><tr><th>Status</th><th>Signal</th><th>Period</th><th className="num">CAGR</th><th className="num">Sharpe</th><th className="num">Max DD</th><th className="num">DSR</th><th>Created</th></tr></thead>
                <tbody>{d.map((r) => (
                  <tr key={r.id}>
                    <td><Link href={`/backtests/${r.id}`}><Badge value={r.status} /></Link></td>
                    <td><code>{r.signal}</code></td><td className="small">{date(r.start)} → {date(r.end)}</td>
                    <td className={`num ${signClass(r.metrics?.cagr)}`}>{pct(r.metrics?.cagr)}</td>
                    <td className="num">{num(r.metrics?.sharpe)}</td><td className="num">{pct(r.metrics?.max_drawdown)}</td>
                    <td className="num" title="Probability the true Sharpe beats the best expected by chance">{pct(r.deflated_sharpe, 0)}</td>
                    <td className="muted small">{dateTime(r.created_at)}</td>
                  </tr>))}
                </tbody>
              </table></div>
            )}
          </Load>
        </Card>
      </div>
    </>
  );
}

export default function BacktestsPage() {
  return <Suspense><Inner /></Suspense>;
}
