"use client";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { Card, Load, Notice, PageHead } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import type { Portfolio } from "@/lib/types";

function Inner() {
  const symbol = useSearchParams().get("symbol");
  const pfs = useApi<Portfolio[]>("portfolios");
  const q = symbol ? `?symbol=${encodeURIComponent(symbol)}` : "";
  return (
    <>
      <PageHead title="Paper trading" sub="Simulated orders filled on the next session's real bars. There is no live execution path." />
      <Notice>Orders fill at the next session (market orders at the open plus slippage, at most 10% of the day&apos;s volume).
        Fills are booked into the paper portfolio&apos;s ledger. Shorting is not supported.</Notice>
      <Card title={symbol ? `Choose a paper portfolio for ${symbol}` : "Paper portfolios"}>
        <Load q={pfs} empty={(d) => d.filter((p) => p.is_paper).length === 0 && <>No paper portfolios. <Link href="/portfolios">Create one</Link> and deposit cash first.</>}>
          {(d) => (
            <ul className="notes" style={{ color: "inherit" }}>
              {d.filter((p) => p.is_paper).map((p) => <li key={p.id}><Link href={`/paper/${p.id}${q}`}>{p.name}</Link></li>)}
            </ul>
          )}
        </Load>
      </Card>
    </>
  );
}

export default function PaperIndex() {
  return <Suspense><Inner /></Suspense>;
}
