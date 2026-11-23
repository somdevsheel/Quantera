"use client";
import Link from "next/link";

import { Sparkline } from "@/components/charts";
import { Badge, Card, Load, Notice, PageHead, Progress, StockLink } from "@/components/ui";
import { date, dateTime, num, pct, signClass } from "@/lib/fmt";
import { useApi } from "@/lib/hooks";
import { RUNNING, type FeedItem, type Job, type Overview, type Portfolio } from "@/lib/types";

const KIND_ROUTE: Record<string, string> = { SCAN: "/scanner/", OPTIMIZE: "/builder/", BACKTEST: "/backtests/", TRAIN: "/models?job=" };

export default function Dashboard() {
  const ov = useApi<Overview>("market/overview");
  const jobs = useApi<Job[]>("analysis/jobs", { query: { limit: 8 }, pollMs: 4000, keepPolling: (d) => d.some(RUNNING) });
  const feed = useApi<FeedItem[]>("alerts/feed");
  const pfs = useApi<Portfolio[]>("portfolios");

  return (
    <>
      <PageHead title="Dashboard" sub={ov.data ? <>Market data as of <b>{date(ov.data.latest_market_date)}</b> · lake release <code>{ov.data.lake_release}</code></> : "Market overview"}
                actions={ov.data && <Badge value={ov.data.freshness} title={`${ov.data.calendar_days_since} calendar days since the last market date`} />} />
      <Load q={ov}>
        {(o) => (
          <>
            {o.freshness === "STALE" && (
              <Notice tone="warn">The newest market data is {o.calendar_days_since} days old. {o.note}</Notice>
            )}
            <div className="grid g4">
              {o.indices.map((i) => (
                <div className="card" key={i.index_id}>
                  <div className="row gap" style={{ justifyContent: "space-between" }}>
                    <b>{i.name ?? i.index_id}</b>
                    {i.status !== "OK" && <Badge value={i.status} title={i.message} />}
                  </div>
                  {i.status === "OK" ? (
                    <>
                      <div className="row gap" style={{ justifyContent: "space-between", marginTop: 6 }}>
                        <div className="stat">
                          <b>{num(i.close)}</b>
                          <span className={signClass(i.change_1d)}>{pct(i.change_1d, 2, true)} today</span>
                        </div>
                        <Sparkline values={(i.spark ?? []).map((p) => p.c)} />
                      </div>
                      <div className="row gap small muted" style={{ marginTop: 6 }}>
                        <span>1M <b className={signClass(i.change_1m)}>{pct(i.change_1m, 1, true)}</b></span>
                        <span>1Y <b className={signClass(i.change_1y)}>{pct(i.change_1y, 1, true)}</b></span>
                        {i.pe != null && <span>P/E <b>{num(i.pe, 1)}</b></span>}
                      </div>
                    </>
                  ) : <p className="muted small">No data for this index in the release.</p>}
                </div>
              ))}
            </div>
            <p className="muted small">{o.note}</p>
          </>
        )}
      </Load>

      <div className="grid g2" style={{ marginTop: 14 }}>
        <Card title="Recent jobs" actions={<Link className="btn small" href="/scanner">New scan</Link>}>
          <Load q={jobs} empty={(d) => d.length === 0 && <>No jobs yet. <Link href="/scanner">Run your first scan</Link>.</>}>
            {(d) => (
              <div className="table-wrap"><table>
                <thead><tr><th>Kind</th><th>Status</th><th>As of</th><th>Progress</th><th>Created</th></tr></thead>
                <tbody>{d.map((j) => (
                  <tr key={j.id}>
                    <td><Link href={`${KIND_ROUTE[j.kind]}${j.id}`}>{j.kind}</Link></td>
                    <td><Badge value={j.status} /></td>
                    <td>{date(j.as_of)}</td>
                    <td style={{ minWidth: 90 }}>{RUNNING(j) ? <Progress value={j.progress} /> : `${j.done_items}/${j.total_items}`}</td>
                    <td className="muted">{dateTime(j.created_at)}</td>
                  </tr>))}
                </tbody>
              </table></div>
            )}
          </Load>
        </Card>

        <Card title="Alerts" actions={<Link className="btn small" href="/alerts">All alerts</Link>}>
          <Load q={feed} empty={(d) => d.length === 0 && <>No alerts. <Link href="/alerts">Create an alert rule</Link>.</>}>
            {(d) => (
              <div className="table-wrap"><table>
                <tbody>{d.slice(0, 8).map((a) => (
                  <tr key={a.id}>
                    <td><StockLink route={a.route} symbol={a.symbol} /></td>
                    <td className="wrap">{a.message}</td>
                    <td className="muted">{date(a.bar_date)}</td>
                    <td>{a.acknowledged ? <span className="muted small">seen</span> : <Badge value="NEW" />}</td>
                  </tr>))}
                </tbody>
              </table></div>
            )}
          </Load>
        </Card>

        <Card title="Portfolios" actions={<Link className="btn small" href="/portfolios">Manage</Link>}>
          <Load q={pfs} empty={(d) => d.length === 0 && <>No portfolios. <Link href="/portfolios">Create one</Link> to track holdings and risk.</>}>
            {(d) => (
              <ul className="notes" style={{ color: "inherit" }}>
                {d.map((p) => (
                  <li key={p.id}><Link href={`/portfolios/${p.id}`}>{p.name}</Link> {p.is_paper && <Badge value="PAPER" />}</li>
                ))}
              </ul>
            )}
          </Load>
        </Card>

        <Card title="About these figures">
          <p className="small">Every number comes from the frozen data lake release shown above, with its own as-of date.
            Decisions are research outputs (BUY / WAIT / NO TRADE / AVOID), not orders or advice. Live trading is disabled;
            orders are simulated in paper portfolios only.</p>
        </Card>
      </div>
    </>
  );
}
