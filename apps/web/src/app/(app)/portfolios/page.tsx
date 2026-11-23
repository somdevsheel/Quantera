"use client";
import Link from "next/link";
import { useState } from "react";

import { Badge, Card, Field, FormError, Load, PageHead } from "@/components/ui";
import { api } from "@/lib/api";
import { dateTime, pct } from "@/lib/fmt";
import { useAction, useApi } from "@/lib/hooks";
import type { Portfolio } from "@/lib/types";

export default function PortfoliosPage() {
  const pfs = useApi<Portfolio[]>("portfolios");
  const act = useAction();
  const [f, setF] = useState({ name: "", is_paper: false, max_position_weight: "10" });
  return (
    <>
      <PageHead title="Portfolios" sub="Append-only ledger; holdings, P&L and risk are recomputed from it with corporate actions applied." />
      <div className="grid g2">
        <Card title="Your portfolios">
          <Load q={pfs} empty={(d) => d.length === 0 && "No portfolios yet."}>
            {(d) => (
              <div className="table-wrap"><table>
                <thead><tr><th>Name</th><th>Type</th><th className="num">Max weight</th><th>Created</th></tr></thead>
                <tbody>{d.map((p) => (
                  <tr key={p.id}>
                    <td><Link href={`/portfolios/${p.id}`}>{p.name}</Link></td>
                    <td>{p.is_paper ? <Badge value="PAPER" /> : <span className="muted">Holdings</span>}</td>
                    <td className="num">{pct(p.max_position_weight, 0)}</td>
                    <td className="muted">{dateTime(p.created_at)}</td>
                  </tr>))}
                </tbody>
              </table></div>
            )}
          </Load>
        </Card>
        <Card title="New portfolio">
          <form className="form" onSubmit={async (e) => {
            e.preventDefault();
            const r = await act.run(() => api<Portfolio>("portfolios", { body: {
              name: f.name, is_paper: f.is_paper, max_position_weight: Number(f.max_position_weight) / 100 } }));
            if (r) { setF({ name: "", is_paper: false, max_position_weight: "10" }); pfs.reload(); }
          }}>
            <FormError error={act.error} />
            <Field label="Name"><input required maxLength={120} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
            <Field label="Max position weight (%)"><input type="number" min={1} max={100} value={f.max_position_weight}
                                                          onChange={(e) => setF({ ...f, max_position_weight: e.target.value })} /></Field>
            <label className="check"><input type="checkbox" checked={f.is_paper} onChange={(e) => setF({ ...f, is_paper: e.target.checked })} />
              Paper portfolio (simulated orders, approvals from the builder and trade plans)</label>
            <button className="btn primary" disabled={act.busy}>Create</button>
          </form>
        </Card>
      </div>
    </>
  );
}
