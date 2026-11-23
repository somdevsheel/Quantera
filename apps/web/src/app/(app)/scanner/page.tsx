"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Badge, Card, Field, FormError, Load, PageHead, Progress } from "@/components/ui";
import { api } from "@/lib/api";
import { date, dateTime } from "@/lib/fmt";
import { useAction, useApi } from "@/lib/hooks";
import { RUNNING, type Job, type Universe } from "@/lib/types";

type Catalog = { indices: { index: string; latest_members: number }[];
                 industries: { industry: string; members: number }[]; notes: string[] };

export default function ScannerPage() {
  const router = useRouter();
  const unis = useApi<Universe[]>("universes");
  const cat = useApi<Catalog>("universes/catalog");
  const jobs = useApi<Job[]>("analysis/jobs", { query: { limit: 50 }, pollMs: 4000, keepPolling: (d) => d.some(RUNNING) });
  const act = useAction();
  const [source, setSource] = useState("index:NIFTY 50");
  const [asOf, setAsOf] = useState("");
  const [adv, setAdv] = useState(false);
  const [policy, setPolicy] = useState({ min_adv_value: "", max_fundamental_age_days: "", buy_opportunity: "" });

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const job = await act.run(async () => {
      let universe_id = source.startsWith("u:") ? source.slice(2) : undefined;
      if (!universe_id) {
        const [kind, name] = source.split(/:(.*)/s);
        const body = kind === "index" ? { name, kind: "INDEX", index: name } : { name, kind: "SECTOR", industry: name };
        universe_id = (await api<Universe>("universes", { body })).id;
      }
      const pol = Object.fromEntries(Object.entries(policy).filter(([, v]) => v !== "").map(([k, v]) => [k, Number(v)]));
      return api<Job>("analysis/jobs", { body: { universe_id, as_of: asOf || undefined, policy: pol } });
    });
    if (job) router.push(`/scanner/${job.id}`);
  };

  return (
    <>
      <PageHead title="Scanner" sub="Rank a universe with the versioned decision policy. Jobs run in the background; you can leave this page." />
      <div className="grid g2">
        <Card title="New scan">
          <form className="form" onSubmit={submit}>
            <FormError error={act.error} />
            <Field label="Universe">
              <select value={source} onChange={(e) => setSource(e.target.value)}>
                {unis.data && unis.data.length > 0 && (
                  <optgroup label="Your universes">
                    {unis.data.map((u) => <option key={u.id} value={`u:${u.id}`}>{u.name} ({u.kind.toLowerCase()})</option>)}
                  </optgroup>
                )}
                <optgroup label="Index (membership snapshot)">
                  {(cat.data?.indices ?? [{ index: "NIFTY 50", latest_members: 50 }]).map((i) =>
                    <option key={i.index} value={`index:${i.index}`}>{i.index} · {i.latest_members}</option>)}
                </optgroup>
                {cat.data && (
                  <optgroup label="Industry">
                    {cat.data.industries.map((i) => <option key={i.industry} value={`industry:${i.industry}`}>{i.industry} · {i.members}</option>)}
                  </optgroup>
                )}
              </select>
            </Field>
            <Field label="As of" hint="Blank = latest market date in the lake. Earlier dates use only data known then.">
              <input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} />
            </Field>
            <label className="check"><input type="checkbox" checked={adv} onChange={(e) => setAdv(e.target.checked)} /> Policy overrides</label>
            {adv && (
              <div className="form cols">
                <Field label="Min ADV (₹)"><input type="number" min={0} value={policy.min_adv_value} placeholder="50000000"
                                                    onChange={(e) => setPolicy({ ...policy, min_adv_value: e.target.value })} /></Field>
                <Field label="Max fundamentals age (days)"><input type="number" min={30} max={3650} placeholder="200" value={policy.max_fundamental_age_days}
                                                    onChange={(e) => setPolicy({ ...policy, max_fundamental_age_days: e.target.value })} /></Field>
                <Field label="BUY opportunity ≥"><input type="number" min={50} max={100} placeholder="70" value={policy.buy_opportunity}
                                                    onChange={(e) => setPolicy({ ...policy, buy_opportunity: e.target.value })} /></Field>
              </div>
            )}
            <button className="btn primary" disabled={act.busy}>{act.busy ? "Queuing…" : "Run scan"}</button>
            {cat.data && <ul className="notes">{cat.data.notes.map((n) => <li key={n}>{n}</li>)}</ul>}
          </form>
        </Card>
        <Card title="How decisions are made">
          <ul className="notes" style={{ color: "inherit" }}>
            <li>Five pillars — momentum, quality, growth, valuation, risk — each a percentile within the universe.</li>
            <li><b>BUY</b> needs high opportunity, low uncertainty, adequate liquidity and at least one fresh fundamental pillar.</li>
            <li><b>WAIT</b>: promising but incomplete or uncertain. <b>NO TRADE</b>: no edge. <b>AVOID</b>: risk too high.</li>
            <li>Missing data is never treated as neutral: rows show <i>coverage</i> (complete / partial / stale / unsupported / failed).</li>
            <li>Every result records the policy, feature and data-release versions, so it can be reproduced.</li>
          </ul>
        </Card>
      </div>
      <Card title="Scan jobs" className="stack" >
        <Load q={jobs} empty={(d) => d.filter((j) => j.kind === "SCAN").length === 0 && "No scans yet."}>
          {(d) => (
            <div className="table-wrap"><table>
              <thead><tr><th>Status</th><th>As of</th><th>Progress</th><th>Result</th><th>Created</th></tr></thead>
              <tbody>{d.filter((j) => j.kind === "SCAN").map((j) => (
                <tr key={j.id}>
                  <td><Link href={`/scanner/${j.id}`}><Badge value={j.status} /></Link></td>
                  <td>{date(j.as_of)}</td>
                  <td style={{ minWidth: 120 }}>{RUNNING(j) ? <Progress value={j.progress} /> : `${j.done_items}/${j.total_items}${j.failed_items ? ` · ${j.failed_items} failed` : ""}`}</td>
                  <td className="small">{Object.entries(j.summary?.states ?? {}).map(([k, v]) => `${k} ${v}`).join(" · ") || "—"}</td>
                  <td className="muted">{dateTime(j.created_at)}</td>
                </tr>))}
              </tbody>
            </table></div>
          )}
        </Load>
      </Card>
    </>
  );
}
