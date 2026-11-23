"use client";
import { useParams } from "next/navigation";
import { useState } from "react";

import { HBars, LineChart } from "@/components/charts";
import { Badge, Card, ErrorBox, Field, FormError, KV, Load, Notes, Notice, PageHead, Progress, StockLink } from "@/components/ui";
import { api } from "@/lib/api";
import { date, inr, num, pct, signClass } from "@/lib/fmt";
import { useAction, useApi } from "@/lib/hooks";
import { RUNNING, type Job, type Portfolio } from "@/lib/types";

type R = Record<string, any>;

function Simulation({ id }: { id: string }) {
  const act = useAction();
  const [sim, setSim] = useState<R>();
  return (
    <Card title="Out-of-sample simulation" actions={<button className="btn" disabled={act.busy} onClick={() => act.run(async () => setSim(await api<R>(`portfolio-construction/${id}/simulate`, { method: "POST" })))}>{act.busy ? "Running…" : "Simulate"}</button>}>
      <FormError error={act.error} />
      {!sim ? <p className="muted small">Holds the proposed weights from the as-of date forward on later lake data.</p>
        : sim.status !== "OK" ? <div className="state empty"><Badge value={sim.status} /> {sim.note}</div> : (
          <>
            <KV items={[["Period", `${date(sim.start)} → ${date(sim.end)}`], ["Return", <span key="r" className={signClass(sim.total_return)}>{pct(sim.total_return, 2, true)}</span>],
                        ["Benchmark", pct(sim.benchmark_return, 2, true)], ["Max drawdown", pct(sim.max_drawdown)], ["Realised beta", num(sim.realised_beta)], ["Volatility", pct(sim.volatility)]]} />
            <LineChart ariaLabel="Simulated NAV" height={200} yFormat={(v) => num(v, 3)} series={[{ name: "NAV", points: (sim.curve ?? []).map((c: R) => ({ x: c.date, y: c.nav })) }]} />
            <Notes notes={sim.assumptions} />
          </>
        )}
    </Card>
  );
}

function Approve({ id, prop }: { id: string; prop: R }) {
  const pfs = useApi<Portfolio[]>("portfolios");
  const act = useAction();
  const [note, setNote] = useState("");
  const [pf, setPf] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [done, setDone] = useState<R>();
  const ok = (prop.status === "OPTIMAL" || prop.status === "APPROXIMATE") && prop.all_checks_pass;
  if (done) return <Notice tone="good">Approved for paper trading. {done.booked?.length ? `Booked: ${done.booked.join(", ")}.` : "Tracked by simulation (not booked)."} Live trading is {done.live_trading}.</Notice>;
  return (
    <Card title="Approve for paper trading">
      {!ok ? <p className="muted">Only a feasible proposal whose rounded book passes every check can be approved.</p> : (
        <form className="form" onSubmit={async (e) => { e.preventDefault(); const r = await act.run(() => api<R>(`portfolio-construction/${id}/approve-paper`, { body: { note, confirm, paper_portfolio_id: pf || undefined } })); if (r) setDone(r); }}>
          <FormError error={act.error} />
          <Field label="Book into paper portfolio (optional)" hint="Long-only books can be booked; short legs are tracked by simulation only.">
            <select value={pf} onChange={(e) => setPf(e.target.value)}><option value="">Don&apos;t book (track only)</option>
              {pfs.data?.filter((p) => p.is_paper).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
          </Field>
          <Field label="Review note"><input required minLength={3} maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} /></Field>
          <label className="check"><input type="checkbox" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} /> I reviewed the legs, exposures, costs and risks.</label>
          <button className="btn primary" disabled={act.busy || !confirm}>Approve (paper only)</button>
        </form>
      )}
    </Card>
  );
}

export default function ProposalPage() {
  const { id } = useParams<{ id: string }>();
  const job = useApi<Job>(`portfolio-construction/jobs/${id}`, { pollMs: 2000, keepPolling: RUNNING });
  return (
    <Load q={job}>
      {(j) => {
        const p: R | undefined = j.summary?.proposal;
        if (RUNNING(j)) return <Card title="Optimising…"><Progress value={j.progress} /></Card>;
        if (j.status === "FAILED" || !p) return <ErrorBox error={new Error(j.error ?? "The optimisation failed")} />;
        const x: R = p.exposures ?? {};
        return (
          <>
            <PageHead title={<>{p.label ?? p.mode} <Badge value={p.status} /></>} sub={<>As of {date(p.as_of)} · capital {inr(p.capital)} · lake <code>{p.lake_release}</code> · {p.solver?.name}</>} />
            <Notice>{p.disclaimer}</Notice>
            {p.status === "INFEASIBLE" && (
              <Card title="Why no book is possible">
                <Notes notes={p.diagnostics?.hints} />
                {p.diagnostics?.binding_groups && <KV items={Object.entries(p.diagnostics.binding_groups).map(([k, v]) => [k.replace(/_/g, " "), `violation ${num(v as number, 3)}`])} />}
                {p.alternatives?.length > 0 && <p>Alternatives: {p.alternatives.map((a: string) => <Badge key={a} value={a} />)}</p>}
              </Card>
            )}
            {p.status === "NO TRADE" && <Notice tone="warn">No trade: the optimal book is too small to be worth trading.</Notice>}
            <Notes notes={p.notes} />
            {p.legs?.length > 0 && (
              <>
                <div className="grid g4">
                  <div className="card stat"><span>Gross / net</span><b>{pct(x.gross)} / {pct(x.net, 1, true)}</b><span>Long {pct(x.long)} · short {pct(x.short)}</span></div>
                  <div className="card stat"><span>Beta</span><b>{num(x.beta)}</b><span>{x.names_long} long · {x.names_short} short</span></div>
                  <div className="card stat"><span>Volatility (ann.)</span><b>{pct(x.volatility)}</b><span>Expected return {pct(x.expected_return, 2, true)}</span></div>
                  <div className="card stat"><span>Loss if every stop hits</span><b>{inr(p.stop_risk?.all_stops_hit)}</b><span>{p.stop_risk?.method}</span></div>
                </div>
                <Card title="Legs">
                  <div className="table-wrap"><table>
                    <thead><tr><th>Stock</th><th>Side</th><th className="num">Weight</th><th className="num">Shares</th><th className="num">Price</th><th className="num">Notional</th>
                      <th className="num">Beta</th><th className="num">Exp. return</th><th className="num">Stop dist.</th><th className="num">Loss at stop</th><th className="num">Risk contr.</th><th>Sector</th></tr></thead>
                    <tbody>{p.legs.map((l: R) => (
                      <tr key={l.symbol}><td><StockLink symbol={l.symbol} /></td><td><Badge value={l.side} /></td>
                        <td className={`num ${signClass(l.weight)}`}>{pct(l.weight, 2)}</td><td className="num">{num(l.shares, 0)}</td><td className="num">{num(l.price)}</td>
                        <td className="num">{inr(l.notional)}</td><td className="num">{num(l.beta)}</td><td className="num">{pct(l.expected_return, 1, true)}</td>
                        <td className="num">{pct(l.stop_distance_pct)}</td><td className="num">{inr(l.loss_at_stop)}</td><td className="num">{pct(l.risk_contribution)}</td><td className="small muted">{l.sector}</td></tr>))}
                    </tbody>
                  </table></div>
                  {p.hedge && <p className="small"><b>Hedge:</b> {p.hedge.instrument} weight {pct(p.hedge.weight, 1, true)} — {p.hedge.note}</p>}
                </Card>
                <div className="grid g2">
                  <Card title="Sector net exposure"><HBars data={Object.entries(x.sector_net ?? {}).filter(([, v]) => Math.abs(v as number) > 1e-6).map(([label, value]) => ({ label, value: value as number }))} /></Card>
                  <Card title="Constraint checks">
                    <div className="table-wrap"><table><tbody>{(p.checks ?? []).map((c: R) => (
                      <tr key={c.name}><td>{c.name.replace(/_/g, " ")}</td><td className="num">{num(c.value, 3)}</td><td className="muted">{c.limit}</td><td>{c.ok ? <Badge value="OK" /> : <Badge value="FAILED" />}</td></tr>))}</tbody></table></div>
                  </Card>
                  <Card title="Stress tests">
                    <div className="table-wrap"><table><tbody>{(p.stress ?? []).map((s: R) => (
                      <tr key={s.scenario}><td>{s.scenario}</td><td className={`num ${signClass(s.pnl_pct)}`}>{pct(s.pnl_pct, 2, true)}</td><td className="small muted wrap">{s.note}</td></tr>))}</tbody></table></div>
                  </Card>
                  <Card title="Estimation"><KV items={[["Observations", p.estimation?.observations], ["Window", `${date(p.estimation?.window_start)} → ${date(p.estimation?.window_end)}`],
                                                       ["Covariance", p.estimation?.covariance], ["Signal", `${p.signal?.kind} (${p.signal?.classification})`]]} />
                    <p className="muted small">{p.signal?.note}</p></Card>
                </div>
                <Simulation id={id} />
                <Approve id={id} prop={p} />
              </>
            )}
          </>
        );
      }}
    </Load>
  );
}
