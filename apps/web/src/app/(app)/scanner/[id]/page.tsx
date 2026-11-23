"use client";
import { useParams } from "next/navigation";
import { Fragment, useEffect, useState } from "react";

import { Badge, Card, ErrorBox, Load, Loading, Notes, PageHead, Progress, StockLink } from "@/components/ui";
import { api } from "@/lib/api";
import { date, score } from "@/lib/fmt";
import { useAction, useApi } from "@/lib/hooks";
import { RUNNING, type Decision, type Job, type Results } from "@/lib/types";

const STATES = ["BUY", "WAIT", "NO TRADE", "AVOID"];
const PILLARS = ["MOMENTUM", "QUALITY", "GROWTH", "VALUATION", "RISK"] as const;

export default function ScanJobPage() {
  const { id } = useParams<{ id: string }>();
  const job = useApi<Job>(`analysis/jobs/${id}`, { pollMs: 2000, keepPolling: RUNNING });
  const act = useAction();
  const [states, setStates] = useState<string[]>([]);
  const [q, setQ] = useState("");
  const [minOpp, setMinOpp] = useState("");
  const [sort, setSort] = useState<{ by: string; order: "asc" | "desc" }>({ by: "rank", order: "asc" });
  const [pages, setPages] = useState<Results[]>([]);
  const [err, setErr] = useState<unknown>();
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const done = job.data && !RUNNING(job.data);

  const fetchPage = async (cursor?: string) => {
    setLoading(true);
    try {
      const r = await api<Results>(`analysis/jobs/${id}/results`, { query: {
        state: states, q: q || undefined, min_opportunity: minOpp || undefined, sort: sort.by, order: sort.order, limit: 50, cursor } });
      setPages((p) => (cursor ? [...p, r] : [r])); setErr(undefined);
    } catch (e) { setErr(e); } finally { setLoading(false); }
  };
  useEffect(() => { if (done) void fetchPage(); /* eslint-disable-next-line */ }, [done, states.join(), sort.by, sort.order, minOpp]);
  useEffect(() => { if (!done) return; const t = setTimeout(() => void fetchPage(), 300); return () => clearTimeout(t); /* eslint-disable-next-line */ }, [q]);

  const rows: Decision[] = pages.flatMap((p) => p.data);
  const last = pages.at(-1);
  const head = (by: string, label: string, num = false) => (
    <th className={`sortable ${num ? "num" : ""}`} onClick={() => setSort((s) => ({ by, order: s.by === by && s.order === "asc" ? "desc" : "asc" }))}>
      {label}{sort.by === by ? (sort.order === "asc" ? " ▲" : " ▼") : ""}
    </th>
  );

  return (
    <Load q={job}>
      {(j) => (
        <>
          <PageHead title="Scan results" sub={<>As of <b>{date(j.as_of)}</b> · policy <code>{j.versions?.policy}</code> · features <code>{j.versions?.features}</code> · lake <code>{j.versions?.lake_release}</code></>}
                    actions={<>
                      <Badge value={j.status} />
                      {RUNNING(j) && <button className="btn" disabled={act.busy} onClick={() => act.run(async () => { await api(`analysis/jobs/${id}/cancel`, { method: "POST" }); job.reload(); })}>Cancel</button>}
                      {(j.status === "FAILED" || j.status === "CANCELLED") && <button className="btn" disabled={act.busy} onClick={() => act.run(async () => { await api(`analysis/jobs/${id}/retry`, { method: "POST" }); job.reload(); })}>Retry</button>}
                      {done && <a className="btn" href={`/bff/api/analysis/jobs/${id}/export.csv`}>Export CSV</a>}
                    </>} />
          {act.error && <ErrorBox error={act.error} />}
          {RUNNING(j) && (
            <Card title={`Working… ${j.done_items} of ${j.total_items}`}><Progress value={j.progress} />
              <p className="muted small">Partial results appear when the job finishes; you can leave this page.</p></Card>
          )}
          {j.status === "FAILED" && <ErrorBox error={new Error(j.error ?? "The job failed")} />}
          {done && (
            <>
              <div className="grid g4">
                {STATES.map((s) => (
                  <div key={s} className="card stat"><span>{s}</span><b>{j.summary?.states?.[s] ?? 0}</b></div>
                ))}
              </div>
              <Notes notes={j.summary?.notes} />
              <Card title={`${last?.total ?? "…"} stocks`} className="stack"
                    actions={<>
                      <input type="search" placeholder="Filter symbol / name" value={q} onChange={(e) => setQ(e.target.value)} />
                      <input type="number" min={0} max={100} placeholder="Min opportunity" style={{ width: 140 }} value={minOpp} onChange={(e) => setMinOpp(e.target.value)} />
                      {STATES.map((s) => (
                        <label key={s} className="check small"><input type="checkbox" checked={states.includes(s)}
                          onChange={(e) => setStates(e.target.checked ? [...states, s] : states.filter((x) => x !== s))} />{s}</label>
                      ))}
                    </>}>
                {err ? <ErrorBox error={err} retry={() => void fetchPage()} /> : null}
                {!pages.length && loading ? <Loading /> : rows.length === 0 ? <div className="state empty">No stocks match these filters.</div> : (
                  <div className="table-wrap"><table>
                    <thead><tr>
                      {head("rank", "#", true)}{head("symbol", "Stock")}<th>Decision</th><th>Coverage</th>
                      {head("opportunity", "Opportunity", true)}{head("uncertainty", "Uncertainty", true)}
                      {PILLARS.map((p) => <th key={p} className="num">{p.slice(0, 4)}</th>)}<th>Industry</th>
                    </tr></thead>
                    <tbody>{rows.map((r) => (
                      <Fragment key={r.instrument_id}>
                        <tr onClick={() => setOpen(open === r.instrument_id ? null : r.instrument_id)} style={{ cursor: "pointer" }}>
                          <td className="num">{r.rank}</td>
                          <td><StockLink route={r.route} symbol={r.symbol} /> <span className="muted small">{r.name}</span></td>
                          <td><Badge value={r.state} /></td>
                          <td><Badge value={r.coverage} /></td>
                          <td className="num">{score(r.opportunity)}</td>
                          <td className="num">{score(r.uncertainty)}</td>
                          {PILLARS.map((p) => <td key={p} className="num">{r.pillars[p] == null ? <span className="muted" title="missing: not treated as neutral">n/a</span> : score(r.pillars[p])}</td>)}
                          <td className="muted small">{r.industry ?? "—"}</td>
                        </tr>
                        {open === r.instrument_id && (
                          <tr><td colSpan={7 + PILLARS.length} className="wrap">
                            <p><b>{r.explanation}</b></p>
                            <div className="grid g3">
                              <div><h3>Supporting</h3><Notes notes={r.supporting.length ? r.supporting : ["none"]} /></div>
                              <div><h3>Against</h3><Notes notes={r.counter.length ? r.counter : ["none"]} /></div>
                              <div><h3>Policy overrides</h3><Notes notes={r.overrides.length ? r.overrides : ["none"]} /></div>
                            </div>
                          </td></tr>
                        )}
                      </Fragment>))}
                    </tbody>
                  </table></div>
                )}
                {last?.next_cursor && (
                  <div className="row" style={{ justifyContent: "center", marginTop: 10 }}>
                    <button className="btn" disabled={loading} onClick={() => void fetchPage(last.next_cursor!)}>{loading ? "Loading…" : "Load more"}</button>
                  </div>
                )}
              </Card>
            </>
          )}
        </>
      )}
    </Load>
  );
}
