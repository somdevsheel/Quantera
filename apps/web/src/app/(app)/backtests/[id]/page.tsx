"use client";
import { useParams } from "next/navigation";

import { BarChart, HBars, LineChart } from "@/components/charts";
import { Badge, Card, ErrorBox, KV, Load, Notes, Notice, PageHead, Progress } from "@/components/ui";
import { date, inr, inrShort, label, num, pct, signClass } from "@/lib/fmt";
import { useApi } from "@/lib/hooks";
import { RUNNING, type Job } from "@/lib/types";

type R = Record<string, any>;

export default function BacktestPage() {
  const { id } = useParams<{ id: string }>();
  const job = useApi<Job>(`backtests/${id}`, { pollMs: 2500, keepPolling: RUNNING });
  return (
    <Load q={job}>
      {(j) => {
        if (RUNNING(j)) return <Card title={`Running… ${j.done_items}/${j.total_items}`}><Progress value={j.progress} /></Card>;
        if (j.status !== "SUCCEEDED") return <ErrorBox error={new Error(j.error ?? `Backtest ${j.status.toLowerCase()}`)} />;
        const s: R = j.summary;
        const r: R = s.result ?? {};
        const m: R = r.metrics ?? {};
        const cap = r.config?.capital ?? 1;
        const eq: R[] = r.equity ?? [];
        const bench = new Map<string, number>((r.benchmark ?? []).map((b: R) => [b.date, b.level]));
        const wf: R | undefined = s.walk_forward;
        const ds: R = s.deflated_sharpe ?? {};
        return (
          <>
            <PageHead title={<>{label(s.family ?? "Backtest")} · <code>{s.signal}</code></>}
                      sub={<>{date(eq[0]?.date)} → {date(eq.at(-1)?.date)} · {s.universe?.kind} top {s.universe?.top} ({s.universe?.instruments_over_window} instruments over the window) · {r.version}</>} />
            {(s.warnings ?? []).length > 0 && <Notice tone="warn"><Notes notes={s.warnings} /></Notice>}
            <div className="grid g4">
              <div className="card stat"><span>CAGR</span><b className={signClass(m.cagr)}>{pct(m.cagr, 1, true)}</b><span>Total {pct(m.total_return, 0, true)}</span></div>
              <div className="card stat"><span>Sharpe / Sortino</span><b>{num(m.sharpe)} / {num(m.sortino)}</b><span>Vol {pct(m.volatility)}</span></div>
              <div className="card stat"><span>Max drawdown</span><b className="neg">{pct(m.max_drawdown)}</b><span>Calmar {num(m.calmar)}</span></div>
              <div className="card stat"><span>Deflated Sharpe</span><b>{pct(ds.deflated_sharpe_probability, 0)}</b><span>{ds.trials ?? "?"} trial(s) in this family</span></div>
            </div>
            <Card title="Equity vs NIFTY 50 (growth of 1)" className="stack">
              <LineChart ariaLabel="Equity curve versus benchmark" height={280} yFormat={(v) => v.toFixed(2)}
                series={[{ name: "Strategy (net of costs)", points: eq.map((e) => ({ x: e.date, y: e.nav / cap })) },
                         { name: "NIFTY 50", color: "var(--muted)", points: eq.map((e) => ({ x: e.date, y: bench.get(e.date) ?? null })) }]} />
            </Card>
            <div className="grid g2" style={{ marginTop: 14 }}>
              <Card title="Drawdown"><LineChart ariaLabel="Drawdown" height={160} yFormat={(v) => `${(v * 100).toFixed(0)}%`}
                series={[{ name: "Drawdown", color: "var(--neg)", points: eq.map((e) => ({ x: e.date, y: e.drawdown })) }]} /></Card>
              <Card title="Monthly returns"><BarChart ariaLabel="Monthly returns" data={Object.entries(r.monthly ?? {}).map(([k, v]) => ({ label: k, value: v as number }))} /></Card>
              <Card title="Yearly returns">
                <div className="table-wrap"><table><tbody>{Object.entries(r.yearly ?? {}).map(([y, v]) => (
                  <tr key={y}><td>{y}</td><td className={`num ${signClass(v)}`}>{pct(v, 1, true)}</td></tr>))}</tbody></table></div>
              </Card>
              <Card title={`Costs: ${inr(r.costs?.total)} (${pct(m.cost_drag_pct_of_capital, 1)} of capital)`}>
                <HBars format={(v) => inrShort(v)} data={Object.entries(r.costs ?? {}).filter(([k]) => k !== "total").map(([k, v]) => ({ label: k.toUpperCase(), value: v as number })).sort((a, b) => b.value - a.value)} />
              </Card>
            </div>
            <Card title="All metrics" className="stack">
              <KV items={Object.entries(m).filter(([, v]) => typeof v === "number").map(([k, v]) => [label(k),
                /return|cagr|vol|drawdown|var|es_|day|alpha|tracking|rate|month|turnover|pct/.test(k) ? pct(v, 2) : num(v, k === "rebalances" || k === "observations" ? 0 : 3)])} />
            </Card>
            {wf && (
              <Card title="Walk-forward">
                <p className="small">Train {wf.train_days} days, test {wf.test_days} days; parameter sets: {(wf.parameter_sets ?? []).join(", ")}.</p>
                {wf.out_of_sample && <KV items={[["OOS CAGR", pct(wf.out_of_sample.cagr, 1, true)], ["OOS Sharpe", num(wf.out_of_sample.sharpe)], ["OOS max DD", pct(wf.out_of_sample.max_drawdown)]]} />}
                <div className="table-wrap"><table><thead><tr><th>Train end</th><th>Test end</th><th>Chosen</th><th className="num">Train Sharpe</th><th className="num">Test return</th></tr></thead>
                  <tbody>{(wf.folds ?? []).map((fo: R, i: number) => (
                    <tr key={i}><td>{date(fo.train_end)}</td><td>{date(fo.test_end)}</td><td><code>{fo.chosen}</code></td><td className="num">{num(fo.train_sharpe)}</td>
                      <td className={`num ${signClass(fo.test_return)}`}>{pct(fo.test_return, 1, true)}</td></tr>))}</tbody></table></div>
              </Card>
            )}
            <Card title="Deflated Sharpe ratio">
              <KV items={[["Sharpe (annual)", num(ds.sharpe_annual)], ["Trials counted", ds.trials], ["Probability", pct(ds.deflated_sharpe_probability, 1)],
                          ["Skew", num(ds.skew)], ["Kurtosis", num(ds.kurtosis)], ["Observations", ds.observations]]} />
              <p className="muted small">{ds.interpretation}. {ds.note}</p>
            </Card>
            <Card title={`Trades (${r.trade_count ?? 0}; first 200 shown)`}>
              <div className="table-wrap" style={{ maxHeight: 360 }}><table>
                <thead><tr><th>Date</th><th>Instrument</th><th className="num">Value</th><th className="num">Price</th><th className="num">Cost</th></tr></thead>
                <tbody>{(r.trades ?? []).slice(0, 200).map((t: R, i: number) => (
                  <tr key={i}><td>{date(t.date)}</td><td className="mono small">{t.name}</td><td className={`num ${signClass(t.value)}`}>{inr(t.value)}</td>
                    <td className="num">{num(t.price)}</td><td className="num">{inr(t.cost, 2)}</td></tr>))}</tbody>
              </table></div>
            </Card>
            {(r.events ?? []).length > 0 && <Card title="Events"><Notes notes={(r.events as R[]).map((e) => JSON.stringify(e))} /></Card>}
            <p className="muted small">Config: <code>{JSON.stringify(r.config)}</code> <Badge value="RESEARCH" /></p>
          </>
        );
      }}
    </Load>
  );
}
