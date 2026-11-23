"use client";
// The four-field price panel: current price, stop loss, target, trailing stop.
import { Badge } from "@/components/ui";
import { num } from "@/lib/fmt";
import type { PriceLevels as PL } from "@/lib/types";

export default function PriceLevels({ pl }: { pl: PL }) {
  const cells: [string, number | null, string | null][] = [
    ["Current price", pl.current_price, null],
    ["Stop loss", pl.stop_loss.price, pl.stop_loss.state],
    ["Target price", pl.target_price.price, pl.target_price.state],
    ["Trailing stop loss", pl.trailing_stop_loss.price, pl.trailing_stop_loss.state],
  ];
  return (
    <div className="levels">
      {cells.map(([k, v, st]) => (
        <div className="level" key={k}>
          <span>{k}</span>
          <b>{v == null ? "—" : `₹${num(v)}`}</b>
          {st && <div><Badge value={st} /></div>}
        </div>
      ))}
    </div>
  );
}
