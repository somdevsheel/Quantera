"use client";
import { Fragment, useState } from "react";

import { Badge, Card, ErrorBox, Field, FormError, KV, Load, Notes, Notice, PageHead, Progress, StockLink } from "@/components/ui";
import { api } from "@/lib/api";
import { date, dateTime, num, pct, score } from "@/lib/fmt";
import { useAction, useApi } from "@/lib/hooks";
import { RUNNING, type Job } from "@/lib/types";

type R = Record<string, any>;
type Model = { id: string; name: string; version: number; stage: string; model_type: string; target: string;
  horizon: number; created_at: string; label_cutoff: string; gates: Record<string, boolean>;
  performance: R | null; code_version: string; dataset: R; history: R[] };

function Train({ onQueued }: { onQueued: () => void }) {
  const act = useAction();
  const [f, setF] = useState({ name: "excess_return", model_type: "RIDGE", horizon: "20", start: "2018-01-01", end: "",
                               liquid_top: "100", n_folds: "5", embargo_days: "5", cost_bps: "30" });
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  return (
    <Card title="Train a candidate">
      <form className="form" onSubmit={async (e) => {
        e.preventDefault();
        const r = await act.run(() => api<Job>("models/train", { body: { name: f.name, model_type: f.model_type, horizon: Number(f.horizon),
          start: f.start, end: f.end || undefined, liquid_top: Number(f.liquid_top), n_folds: Number(f.n_folds),
          embargo_days: Number(f.embargo_days), cost_bps: Number(f.cost_bps) } }));
        if (r) onQueued();
      }}>
        <FormError error={act.error} />
        <div className="form cols">
          <Field label="Name"><input required pattern="[A-Za-z0-9_\-]+" value={f.name} onChange={set("name")} /></Field>
          <Field label="Model"><select value={f.model_type} onChange={set("model_type")}><option>RIDGE</option><option>LOGIT</option><option>GBM</option></select></Field>
          <Field label="Horizon (days)"><select value={f.horizon} onChange={set("horizon")}>{[5, 20, 60, 120].map((h) => <option key={h}>{h}</option>)}</select></Field>
          <Field label="Start"><input type="date" required value={f.start} onChange={set("start")} /></Field>
          <Field label="End" hint="Blank = latest matured label"><input type="date" value={f.end} onChange={set("end")} /></Field>
          <Field label="Liquid top N"><input type="number" min={20} max={300} value={f.liquid_top} onChange={set("liquid_top")} /></Field>
          <Field label="Folds"><input type="number" min={3} max={10} value={f.n_folds} onChange={set("n_folds")} /></Field>
          <Field label="Embargo (days)"><input type="number" min={0} max={60} value={f.embargo_days} onChange={set("embargo_days")} /></Field>
          <Field label="Round-trip cost (bps)"><input type="number" min={0} max={300} value={f.cost_bps} onChange={set("cost_bps")} /></Field>
        </div>
        <button className="btn primary" disabled={act.busy}>Train</button>
        <p className="muted small">Target: excess return vs NIFTY 50. Purged, embargoed walk-forward against ZERO and MOMENTUM baselines.
          A candidate must pass every gate and be approved by someone other than its trainer before it scores anything.</p>
      </form>
    </Card>
  );
}

function Detail({ m, onChange }: { m: Model; onChange: () => void }) {
  const d = useApi<R>(`models/${m.id}`);
  const mon = useApi<R>(m.stage === "APPROVED" || m.stage === "RETIRED" ? `models/${m.id}/monitor` : null);
  const act = useAction();
  const [note, setNote] = useState("");
  const allPass = Object.values(m.gates).every(Boolean);
  return (
    <div className="stack">
      <FormError error={act.error} />
      <Load q={d}>
        {(x) => (
          <>
            <h3>Promotion gates</h3>
            <div className="table-wrap"><table><tbody>{Object.entries(x.gates_detail ?? {}).map(([k, g]) => (
              <tr key={k}><td>{k.replace(/_/g, " ")}</td><td>{(g as R).passed ? <Badge value="OK" /> : <Badge value="FAILED" />}</td>
                <td className="small wrap mono">{JSON.stringify((g as R).value ?? (g as R).detail ?? "")}</td><td className="small wrap muted">{(g as R).rule ?? ""}</td></tr>))}</tbody></table></div>
            <KV items={[["Label cutoff", date(m.label_cutoff)], ["Code", m.code_version], ["Artifact sha256", <code key="s" className="small">{String(x.artifact_sha256 ?? "").slice(0, 16)}…</code>],
                        ["Dataset", JSON.stringify(m.dataset).slice(0, 80)]]} />
          </>
        )}
      </Load>
      {mon.data && (
        <>
          <h3>Monitoring (realised outcomes)</h3>
          {(mon.data.matured_batches ?? []).length === 0 ? <p className="muted small">No predictions have matured yet ({mon.data.pending ?? 0} pending).</p> : (
            <div className="table-wrap"><table><thead><tr><th>Scored</th><th>Matured</th><th className="num">N</th><th className="num">Realised IC</th><th className="num">Validation IC</th></tr></thead>
              <tbody>{mon.data.matured_batches.map((b: R) => (
                <tr key={b.as_of}><td>{date(b.as_of)}</td><td>{date(b.matured_on)}</td><td className="num">{b.n}</td><td className="num">{num(b.realised_rank_ic, 3)}</td><td className="num">{num(b.validation_ic, 3)}</td></tr>))}</tbody></table></div>
          )}
          <p className="muted small">{mon.data.note}</p>
        </>
      )}
      <h3>History</h3>
      <Notes notes={m.history.map((h) => `${dateTime(h.at)} · ${h.event}${h.note ? ` — ${h.note}` : ""}`)} />
      {m.stage !== "RETIRED" && (
        <div className="row gap">
          <input placeholder="Review note (required)" value={note} onChange={(e) => setNote(e.target.value)} style={{ minWidth: 260 }} />
          {m.stage === "CANDIDATE" && <button className="btn primary" disabled={act.busy || note.length < 3 || !allPass}
            title={allPass ? "" : "Every gate must pass"} onClick={() => act.run(async () => { await api(`models/${m.id}/approve`, { body: { note } }); onChange(); })}>Approve</button>}
          <button className="btn danger" disabled={act.busy || note.length < 3} onClick={() => act.run(async () => { await api(`models/${m.id}/retire`, { body: { note } }); onChange(); })}>Retire</button>
        </div>
      )}
    </div>
  );
}

function Scoring() {
  const act = useAction();
  const [name, setName] = useState("excess_return");
  const [res, setRes] = useState<R>();
  return (
    <Card title="Score the liquid universe with the approved model">
      <div className="row gap">
        <input value={name} onChange={(e) => setName(e.target.value)} aria-label="Model name" />
        <button className="btn" disabled={act.busy} onClick={() => act.run(async () => setRes(await api<R>("models/score", { body: { name } })))}>Score now</button>
        <button className="btn" disabled={act.busy} onClick={() => {
          const note = prompt("Why roll back to the previous approved version?");
          if (note && note.length >= 3) void act.run(async () => setRes(await api<R>("models/rollback", { body: { note }, query: { name } })));
        }}>Roll back</button>
      </div>
      <FormError error={act.error} />
      {res && (
        <div className="stack" style={{ marginTop: 12 }}>
          <div><Badge value={res.state} /> {res.reason ?? res.meaning}</div>
          {res.drift?.psi && <KV items={Object.entries(res.drift.psi).map(([k, v]) => [k, <span key={k} className={(v as number) >= res.drift.block_at ? "neg" : (v as number) >= res.drift.warn_at ? "" : "pos"}>PSI {num(v as number, 3)}</span>])} />}
          <Notes notes={res.notes} />
          {(res.rows ?? []).length > 0 && (
            <div className="table-wrap" style={{ maxHeight: 400 }}><table><thead><tr><th>Stock</th><th className="num">Score</th><th className="num">Percentile</th><th className="num">90% interval</th></tr></thead>
              <tbody>{res.rows.map((r: R) => (
                <tr key={r.symbol}><td><StockLink symbol={r.symbol} /></td><td className="num">{num(r.score, 4)}</td><td className="num">{score(r.percentile)}</td>
                  <td className="num">{r.interval_90 ? `${num(r.interval_90[0], 3)} … ${num(r.interval_90[1], 3)}` : "—"}</td></tr>))}</tbody></table></div>
          )}
          {res.disclaimer && <p className="muted small">{res.disclaimer}</p>}
        </div>
      )}
    </Card>
  );
}

export default function ModelsPage() {
  const models = useApi<Model[]>("models");
  const jobs = useApi<Job[]>("analysis/jobs", { query: { limit: 30 }, pollMs: 4000, keepPolling: (d) => d.some((j) => j.kind === "TRAIN" && RUNNING(j)) });
  const [open, setOpen] = useState<string | null>(null);
  const training = (jobs.data ?? []).filter((j) => j.kind === "TRAIN" && (RUNNING(j) || j.status === "FAILED")).slice(0, 5);
  return (
    <>
      <PageHead title="Models" sub="ML lifecycle: train → gates → four-eyes approval → scoring → monitoring → retire or roll back." />
      <Notice>Model outputs are research evidence with stated uncertainty; they never place orders.</Notice>
      <div className="grid g2">
        <Train onQueued={() => { jobs.reload(); }} />
        <Scoring />
      </div>
      {training.length > 0 && (
        <Card title="Training jobs">
          {training.map((j) => (
            <div key={j.id} className="row gap" style={{ marginBottom: 6 }}>
              <Badge value={j.status} /> <span className="small muted">{dateTime(j.created_at)}</span>
              {RUNNING(j) ? <div className="grow"><Progress value={j.progress} /></div> : <span className="small neg">{j.error}</span>}
            </div>
          ))}
        </Card>
      )}
      <div style={{ height: 14 }} />
      <Card title="Registry">
        {models.error && <ErrorBox error={models.error} />}
        <Load q={models} empty={(d) => d.length === 0 && "No models trained yet."}>
          {(d) => (
            <div className="table-wrap"><table>
              <thead><tr><th>Name</th><th className="num">Ver.</th><th>Stage</th><th>Type</th><th className="num">Horizon</th><th>Gates</th><th>Validation IC</th><th>Created</th><th /></tr></thead>
              <tbody>{d.map((m) => (
                <Fragment key={m.id}>
                  <tr>
                    <td>{m.name}</td><td className="num">{m.version}</td><td><Badge value={m.stage} /></td><td>{m.model_type}</td><td className="num">{m.horizon}d</td>
                    <td className="small">{Object.values(m.gates).filter(Boolean).length}/{Object.keys(m.gates).length} pass</td>
                    <td className="small mono">{m.performance ? `${num((m.performance as R).model_ic, 3)} vs ${num((m.performance as R).baseline_ic, 3)} (t ${num((m.performance as R).t_stat, 1)})` : "—"}</td>
                    <td className="muted small">{dateTime(m.created_at)}</td>
                    <td><button className="btn small" onClick={() => setOpen(open === m.id ? null : m.id)}>{open === m.id ? "Hide" : "Review"}</button></td>
                  </tr>
                  {open === m.id && <tr><td colSpan={9} className="wrap"><Detail m={m} onChange={models.reload} /></td></tr>}
                </Fragment>))}
              </tbody>
            </table></div>
          )}
        </Load>
      </Card>
      <p className="muted small">Performance gate: rank IC ≥ best baseline + 0.01 with t ≥ 2; LOGIT also needs calibration error ≤ 0.05. {pct(0.25, 0)} PSI drift blocks scoring.</p>
    </>
  );
}
