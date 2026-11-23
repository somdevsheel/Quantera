"use client";
import { useParams } from "next/navigation";

import { Badge, Card, KV, Load, Notice, PageHead, StockLink } from "@/components/ui";
import { date, num, pct, score } from "@/lib/fmt";
import { useApi } from "@/lib/hooks";

type R = Record<string, any>;

export default function SharedPage() {
  const { id } = useParams<{ id: string }>();
  const q = useApi<R>(`shared/${id}`);
  return (
    <Load q={q}>
      {(d) => (
        <>
          <PageHead title={<>Shared {String(d.resource_type).toLowerCase()} <Badge value="READ ONLY" /></>}
                    sub={d.expires_at ? `Access until ${date(d.expires_at)}` : "Access until the owner revokes it"} />
          <Notice>This is a read-only copy shared with you. The owner can revoke it at any time.</Notice>
          {d.resource_type === "WATCHLIST" && (
            <Card title={d.name}>
              {(d.items ?? []).length === 0 ? <div className="state empty">Empty watchlist.</div> : (
                <div className="row gap">{d.items.map((i: R) => <StockLink key={i.symbol} symbol={i.symbol} route={`/stocks/${i.exchange}/${i.symbol}`} />)}</div>
              )}
            </Card>
          )}
          {d.resource_type === "SCAN" && (
            <Card title={`Scan as of ${date(d.as_of)}`}>
              <KV items={Object.entries(d.summary?.states ?? {}).map(([k, v]) => [k, String(v)])} />
              <div className="table-wrap" style={{ marginTop: 10 }}><table>
                <thead><tr><th className="num">#</th><th>Stock</th><th>Decision</th><th className="num">Opportunity</th><th>Why</th></tr></thead>
                <tbody>{(d.results ?? []).map((r: R) => (
                  <tr key={r.symbol}><td className="num">{r.rank}</td><td><StockLink symbol={r.symbol} /></td><td><Badge value={r.state} /></td>
                    <td className="num">{score(r.opportunity)}</td><td className="wrap small">{r.explanation}</td></tr>))}</tbody>
              </table></div>
            </Card>
          )}
          {d.resource_type === "BACKTEST" && (
            <Card title={`Backtest: ${d.summary?.signal ?? ""}`}>
              <KV items={Object.entries(d.summary?.result?.metrics ?? {}).filter(([, v]) => typeof v === "number").slice(0, 16)
                .map(([k, v]) => [k.replace(/_/g, " "), /return|cagr|vol|drawdown/.test(k) ? pct(v) : num(v, 3)])} />
              <p className="muted small">Trade lists are not included in shares.</p>
            </Card>
          )}
        </>
      )}
    </Load>
  );
}
