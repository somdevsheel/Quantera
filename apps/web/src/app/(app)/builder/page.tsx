"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Badge, Card, Field, FormError, Load, PageHead, Progress } from "@/components/ui";
import { api } from "@/lib/api";
import { date, dateTime } from "@/lib/fmt";
import { useAction, useApi } from "@/lib/hooks";
import { RUNNING, type Job, type Universe } from "@/lib/types";

const MODES = {
  LONG_SHORT: "Long/short, dollar- and beta-neutral (needs shortable names)",
  INDEX_HEDGED: "Long book hedged with a short NIFTY 50 position",
  LOW_BETA_LONG_ONLY: "Long-only, low beta (labelled alternative, not neutral)",
} as const;

export default function BuilderPage() {
  const router = useRouter();
  const unis = useApi<Universe[]>("universes");
  const jobs = useApi<Job[]>("analysis/jobs", { query: { limit: 50 }, pollMs: 4000, keepPolling: (d) => d.some(RUNNING) });
  const act = useAction();
  const [f, setF] = useState({
    universe_id: "", as_of: "", mode: "INDEX_HEDGED" as keyof typeof MODES, capital: "1000000",
    shortable: "", restricted: "", gross_max: "1", max_weight: "10", net_tol: "2", beta_tol: "0.05",
    sector_neutral: false, momentum_neutral: false, vol_target: "", horizon_days: "21", cost_bps: "10",
  });
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setF({ ...f, [k]: e.target.type === "checkbox" ? (e.target as HTMLInputElement).checked : e.target.value });
  const list = (s: string) => s.split(/[\s,]+/).map((x) => x.trim().toUpperCase()).filter(Boolean);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const job = await act.run(() => api<Job>("portfolio-construction/jobs", { body: {
      universe_id: f.universe_id, as_of: f.as_of || undefined, mode: f.mode, capital: Number(f.capital),
      shortable: list(f.shortable), restricted: list(f.restricted), gross_max: Number(f.gross_max),
      max_weight: Number(f.max_weight) / 100, net_tol: Number(f.net_tol) / 100, beta_tol: Number(f.beta_tol),
      sector_neutral: f.sector_neutral, factor_neutral: f.momentum_neutral ? ["MOMENTUM"] : [],
      vol_target: f.vol_target ? Number(f.vol_target) / 100 : undefined, horizon_days: Number(f.horizon_days),
      cost_bps: Number(f.cost_bps),
      dollar_neutral: f.mode === "LONG_SHORT", beta_neutral: f.mode === "LONG_SHORT",
    } }));
    if (job) router.push(`/builder/${job.id}`);
  };

  return (
    <>
      <PageHead title="Market-neutral builder" sub="Optimise a book under exposure, liquidity, cost and risk constraints. Proposals are not orders; approval is for paper trading only." />
      <div className="grid g2">
        <Card title="Configure">
          <form className="form" onSubmit={submit}>
            <FormError error={act.error} />
            <Field label="Universe" hint="Create one under Universes & watchlists.">
              <select required value={f.universe_id} onChange={set("universe_id")}>
                <option value="">Choose…</option>
                {unis.data?.map((u) => <option key={u.id} value={u.id}>{u.name} ({u.kind.toLowerCase()})</option>)}
              </select>
            </Field>
            <Field label="Mode"><select value={f.mode} onChange={set("mode")}>
              {Object.entries(MODES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
            <div className="form cols">
              <Field label="Capital (₹)"><input type="number" min={1} required value={f.capital} onChange={set("capital")} /></Field>
              <Field label="As of" hint="Blank = latest. Use a past date to simulate out of sample."><input type="date" value={f.as_of} onChange={set("as_of")} /></Field>
              <Field label="Gross max (×)"><input type="number" step="0.1" min={0.1} max={4} value={f.gross_max} onChange={set("gross_max")} /></Field>
              <Field label="Max weight (%)"><input type="number" min={1} max={100} value={f.max_weight} onChange={set("max_weight")} /></Field>
              <Field label="Net tolerance (%)"><input type="number" step="0.5" min={0} max={100} value={f.net_tol} onChange={set("net_tol")} /></Field>
              <Field label="Beta tolerance"><input type="number" step="0.01" min={0} max={2} value={f.beta_tol} onChange={set("beta_tol")} /></Field>
              <Field label="Vol target (%/yr)"><input type="number" min={1} max={100} placeholder="none" value={f.vol_target} onChange={set("vol_target")} /></Field>
              <Field label="Horizon (days)"><input type="number" min={5} max={252} value={f.horizon_days} onChange={set("horizon_days")} /></Field>
              <Field label="Cost (bps)"><input type="number" min={0} max={200} value={f.cost_bps} onChange={set("cost_bps")} /></Field>
            </div>
            {f.mode === "LONG_SHORT" && (
              <Field label="Shortable symbols" hint="Only these can be shorted (Indian cash equities need SLB or F&O). Comma or space separated.">
                <textarea rows={2} value={f.shortable} onChange={set("shortable")} placeholder="e.g. RELIANCE HDFCBANK INFY" />
              </Field>
            )}
            <Field label="Restricted symbols" hint="Never traded."><input value={f.restricted} onChange={set("restricted")} /></Field>
            <div className="row gap-lg">
              <label className="check"><input type="checkbox" checked={f.sector_neutral} onChange={set("sector_neutral")} /> Sector-neutral</label>
              <label className="check"><input type="checkbox" checked={f.momentum_neutral} onChange={set("momentum_neutral")} /> Momentum-neutral</label>
            </div>
            <button className="btn primary" disabled={act.busy || !f.universe_id}>{act.busy ? "Queuing…" : "Build proposal"}</button>
          </form>
        </Card>
        <Card title="Proposals">
          <Load q={jobs} empty={(d) => d.filter((j) => j.kind === "OPTIMIZE").length === 0 && "No proposals yet."}>
            {(d) => (
              <div className="table-wrap"><table>
                <thead><tr><th>Status</th><th>Result</th><th>As of</th><th>Created</th></tr></thead>
                <tbody>{d.filter((j) => j.kind === "OPTIMIZE").map((j) => (
                  <tr key={j.id}>
                    <td><Link href={`/builder/${j.id}`}><Badge value={j.status} /></Link>{RUNNING(j) && <Progress value={j.progress} />}</td>
                    <td><Badge value={j.summary?.proposal?.status} /> <span className="small muted">{j.summary?.proposal?.mode}</span></td>
                    <td>{date(j.as_of)}</td><td className="muted">{dateTime(j.created_at)}</td>
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
