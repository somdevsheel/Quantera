"use client";
import Link from "next/link";
import { useState } from "react";

import { Badge, Card, Load, Notice, PageHead } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import type { Family } from "@/lib/types";

type Library = { families: Family[]; counts: Record<string, number>; statuses: string[]; signals: string[]; note: string };

export default function StrategiesPage() {
  const q = useApi<Library>("strategies");
  const [filter, setFilter] = useState("");
  return (
    <>
      <PageHead title="Strategy library" sub="Twenty strategy families with honest statuses. Nothing is promoted above RESEARCH without recorded evidence." />
      <Load q={q}>
        {(lib) => (
          <>
            <Notice>{lib.note}</Notice>
            <div className="row gap" style={{ marginBottom: 12 }}>
              <button className={`btn small ${filter === "" ? "primary" : ""}`} onClick={() => setFilter("")}>All {lib.families.length}</button>
              {Object.entries(lib.counts).map(([k, v]) => (
                <button key={k} className={`btn small ${filter === k ? "primary" : ""}`} onClick={() => setFilter(k)}>{k.replace(/_/g, " ")} {v}</button>
              ))}
            </div>
            <div className="grid g2">
              {lib.families.filter((f) => !filter || f.status === filter).map((f) => (
                <Card key={f.code} title={<>{f.name} <Badge value={f.status} /></>}
                      actions={f.signals.length > 0 && <Link className="btn small" href={`/backtests?signal=${f.signals[0]}`}>Backtest</Link>}>
                  <dl className="kv" style={{ gridTemplateColumns: "1fr" }}>
                    <div><dt>Needs</dt><dd>{f.required_data.join(", ") || "—"}</dd></div>
                    {f.implementation && <div><dt>Implementation</dt><dd>{f.implementation}</dd></div>}
                    {f.validation && <div><dt>Validation</dt><dd style={{ fontWeight: 400 }}>{f.validation}</dd></div>}
                    {f.limitations && <div><dt>Limitations</dt><dd style={{ fontWeight: 400 }}>{f.limitations}</dd></div>}
                    {f.blocked_by.length > 0 && <div><dt>Blocked by</dt><dd style={{ fontWeight: 400 }}>{f.blocked_by.join("; ")}</dd></div>}
                    {f.signals.length > 0 && <div><dt>Signals</dt><dd>{f.signals.map((s) => <code key={s}>{s} </code>)}</dd></div>}
                  </dl>
                </Card>
              ))}
            </div>
          </>
        )}
      </Load>
    </>
  );
}
