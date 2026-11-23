"use client";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { Badge, Card, Field, FormError, Load, Notice, PageHead, StockLink } from "@/components/ui";
import { api } from "@/lib/api";
import { date, dateTime, num } from "@/lib/fmt";
import { useAction, useApi } from "@/lib/hooks";
import type { AlertRule, FeedItem } from "@/lib/types";

const KINDS: Record<string, { label: string; hint: string; def: string }> = {
  PRICE_ABOVE: { label: "Price at or above", hint: "level in ₹", def: "" },
  PRICE_BELOW: { label: "Price at or below", hint: "level in ₹", def: "" },
  MOVE_1D: { label: "Daily move of at least", hint: "fraction, e.g. 0.05 = 5%", def: "0.05" },
  VOLUME_SPIKE: { label: "Volume spike", hint: "multiple of the 20-day median", def: "3" },
  RSI_ABOVE: { label: "RSI(14) above", hint: "0–100", def: "70" },
  RSI_BELOW: { label: "RSI(14) below", hint: "0–100", def: "30" },
  DRAWDOWN_52W: { label: "Below 52-week high by", hint: "fraction, e.g. 0.2 = 20%", def: "0.2" },
  DECISION_CHANGE: { label: "Scan decision changes", hint: "no threshold", def: "0" },
};

function Inner() {
  const params = useSearchParams();
  const rules = useApi<AlertRule[]>("alerts/rules");
  const [unack, setUnack] = useState(false);
  const feed = useApi<FeedItem[]>("alerts/feed", { query: { unacknowledged: unack || undefined } });
  const act = useAction();
  const [ev, setEv] = useState<Record<string, number>>();
  const [f, setF] = useState({ name: "", kind: "PRICE_ABOVE", symbol: params.get("symbol") ?? "", threshold: "", cooldown_days: "1", push: false });
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setF({ ...f, [k]: e.target.type === "checkbox" ? (e.target as HTMLInputElement).checked : e.target.value,
           ...(k === "kind" ? { threshold: KINDS[e.target.value].def } : {}) });

  return (
    <>
      <PageHead title="Alerts" sub="Rules are checked against the latest daily bar; each rule fires at most once per bar and then waits for its cooldown."
                actions={<button className="btn" disabled={act.busy} onClick={() => act.run(async () => { setEv(await api("alerts/evaluate", { method: "POST" })); feed.reload(); rules.reload(); })}>Evaluate now</button>} />
      <FormError error={act.error} />
      {ev && <Notice tone="good">Checked {ev.rules} rule(s); {ev.triggered} triggered.</Notice>}
      <div className="grid g2">
        <Card title="New rule">
          <form className="form" onSubmit={async (e) => {
            e.preventDefault();
            const r = await act.run(() => api("alerts/rules", { body: { name: f.name || `${f.symbol} ${KINDS[f.kind].label}`, kind: f.kind,
              symbol: f.symbol.trim().toUpperCase(), threshold: Number(f.threshold || 0), cooldown_days: Number(f.cooldown_days),
              channels: f.push ? ["IN_APP", "PUSH"] : ["IN_APP"] } }));
            if (r) { setF({ ...f, name: "", threshold: KINDS[f.kind].def }); rules.reload(); }
          }}>
            <div className="form cols">
              <Field label="Symbol (NSE)"><input required value={f.symbol} onChange={set("symbol")} /></Field>
              <Field label="Condition"><select value={f.kind} onChange={set("kind")}>{Object.entries(KINDS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}</select></Field>
              {f.kind !== "DECISION_CHANGE" && <Field label="Threshold" hint={KINDS[f.kind].hint}><input type="number" min={0} step="any" required value={f.threshold} onChange={set("threshold")} /></Field>}
              <Field label="Cooldown (days)"><input type="number" min={0} max={365} value={f.cooldown_days} onChange={set("cooldown_days")} /></Field>
              <Field label="Name (optional)"><input maxLength={120} value={f.name} onChange={set("name")} /></Field>
            </div>
            <label className="check"><input type="checkbox" checked={f.push} onChange={set("push")} /> Also push to my Android device (shows only the symbol and rule name on a locked screen)</label>
            <button className="btn primary" disabled={act.busy}>Create rule</button>
          </form>
        </Card>
        <Card title="Rules">
          <Load q={rules} empty={(d) => d.length === 0 && "No rules yet."}>
            {(d) => (
              <div className="table-wrap"><table>
                <thead><tr><th>Name</th><th>Stock</th><th>Condition</th><th className="num">Threshold</th><th>Last fired</th><th /></tr></thead>
                <tbody>{d.map((r) => (
                  <tr key={r.id}>
                    <td>{r.name}</td><td><StockLink symbol={r.symbol} /></td><td className="small">{KINDS[r.kind]?.label ?? r.kind}</td>
                    <td className="num">{r.kind === "DECISION_CHANGE" ? "—" : num(r.threshold, r.threshold < 1 ? 2 : 2)}</td><td>{date(r.last_triggered_on)}</td>
                    <td><button className="btn small danger" disabled={act.busy} onClick={() => act.run(async () => { await api(`alerts/rules/${r.id}`, { method: "DELETE" }); rules.reload(); })}>Delete</button></td>
                  </tr>))}
                </tbody>
              </table></div>
            )}
          </Load>
        </Card>
      </div>
      <div style={{ height: 14 }} />
      <Card title="Feed" actions={<label className="check small"><input type="checkbox" checked={unack} onChange={(e) => setUnack(e.target.checked)} /> Unseen only</label>}>
        <Load q={feed} empty={(d) => d.length === 0 && "Nothing here."}>
          {(d) => (
            <div className="table-wrap"><table>
              <thead><tr><th>Bar</th><th>Stock</th><th>Source</th><th>Message</th><th>Raised</th><th /></tr></thead>
              <tbody>{d.map((a) => (
                <tr key={`${a.source}-${a.id}`}>
                  <td>{date(a.bar_date)}</td><td><StockLink route={a.route} symbol={a.symbol} /></td>
                  <td><Badge value={a.source === "rule" ? a.kind : a.kind ?? "PLAN"} /></td><td className="wrap">{a.message}</td>
                  <td className="muted small">{dateTime(a.created_at)}</td>
                  <td>{a.source === "rule" && !a.acknowledged && (
                    <button className="btn small" onClick={() => act.run(async () => { await api(`alerts/${a.id}/acknowledge`, { method: "POST" }); feed.reload(); })}>Mark seen</button>)}</td>
                </tr>))}
              </tbody>
            </table></div>
          )}
        </Load>
      </Card>
    </>
  );
}

export default function AlertsPage() {
  return <Suspense><Inner /></Suspense>;
}
