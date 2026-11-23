"use client";
// Small SVG charts. Colours come from CSS variables so they follow light/dark themes.
import { useMemo, useRef, useState } from "react";

export type Pt = { x: string; y: number | null };
export type Series = { name: string; points: Pt[]; color?: string; dashed?: boolean; area?: boolean };
export type HLine = { y: number; label: string; color?: string };

const PALETTE = ["var(--c1)", "var(--c2)", "var(--c3)", "var(--c4)", "var(--c5)"];

function niceTicks(lo: number, hi: number, n = 4): number[] {
  if (!(hi > lo)) return [lo];
  const raw = (hi - lo) / n;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10));
  return out;
}

export function LineChart({ series, height = 240, yFormat = (v) => v.toFixed(2), hlines = [],
                            log = false, ariaLabel }: {
  series: Series[]; height?: number; yFormat?: (v: number) => string; hlines?: HLine[];
  log?: boolean; ariaLabel: string;
}) {
  const W = 800, H = height, L = 56, R = 12, T = 10, B = 24;
  const ref = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<number | null>(null);

  const xs = useMemo(() => {
    const longest = series.reduce((a, s) => (s.points.length > a.length ? s.points : a), [] as Pt[]);
    return longest.map((p) => p.x);
  }, [series]);
  const tf = (v: number) => (log ? Math.log(v) : v);
  const vals = series.flatMap((s) => s.points.map((p) => p.y)).concat(hlines.map((h) => h.y))
    .filter((v): v is number => v !== null && Number.isFinite(v) && (!log || v > 0));
  if (!xs.length || !vals.length) return <div className="state empty">No data to chart.</div>;
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (lo === hi) { lo -= 1; hi += 1; }
  const pad = (hi - lo) * 0.05;
  lo -= pad; hi += pad;
  const sx = (i: number) => L + (xs.length === 1 ? 0 : (i / (xs.length - 1)) * (W - L - R));
  const sy = (v: number) => T + (1 - (tf(v) - tf(lo)) / (tf(hi) - tf(lo))) * (H - T - B);
  const idx = new Map(xs.map((x, i) => [x, i]));
  const ticks = niceTicks(lo, hi);
  const xticks = [0, Math.floor(xs.length / 3), Math.floor((2 * xs.length) / 3), xs.length - 1]
    .filter((v, i, a) => a.indexOf(v) === i);

  const path = (s: Series) => {
    let d = "", pen = false;
    for (const p of s.points) {
      const i = idx.get(p.x);
      if (i === undefined || p.y === null || !Number.isFinite(p.y)) { pen = false; continue; }
      d += `${pen ? "L" : "M"}${sx(i).toFixed(1)},${sy(p.y).toFixed(1)}`;
      pen = true;
    }
    return d;
  };

  const onMove = (e: React.MouseEvent) => {
    const box = ref.current?.getBoundingClientRect();
    if (!box) return;
    const x = ((e.clientX - box.left) / box.width) * W;
    const i = Math.round(((x - L) / (W - L - R)) * (xs.length - 1));
    setHover(Math.max(0, Math.min(xs.length - 1, i)));
  };

  return (
    <div className="chart">
      <svg ref={ref} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={ariaLabel}
           onMouseMove={onMove} onMouseLeave={() => setHover(null)} preserveAspectRatio="none">
        {ticks.map((t) => (
          <g key={t}>
            <line x1={L} x2={W - R} y1={sy(t)} y2={sy(t)} className="grid" />
            <text x={L - 6} y={sy(t) + 4} className="axis" textAnchor="end">{yFormat(t)}</text>
          </g>
        ))}
        {xticks.map((i) => (
          <text key={i} x={sx(i)} y={H - 6} className="axis"
                textAnchor={i === 0 ? "start" : i === xs.length - 1 ? "end" : "middle"}>{xs[i]}</text>
        ))}
        {hlines.map((h) => (
          <g key={h.label}>
            <line x1={L} x2={W - R} y1={sy(h.y)} y2={sy(h.y)} stroke={h.color ?? "var(--muted)"}
                  strokeDasharray="5 4" strokeWidth={1.2} />
            <text x={W - R - 4} y={sy(h.y) - 4} className="axis" textAnchor="end"
                  fill={h.color ?? "var(--muted)"}>{h.label} {yFormat(h.y)}</text>
          </g>
        ))}
        {series.map((s, k) => (
          <path key={s.name} d={path(s)} fill="none" stroke={s.color ?? PALETTE[k % PALETTE.length]}
                strokeWidth={1.6} strokeDasharray={s.dashed ? "4 3" : undefined}
                vectorEffect="non-scaling-stroke" />
        ))}
        {hover !== null && <line x1={sx(hover)} x2={sx(hover)} y1={T} y2={H - B} className="cross" />}
      </svg>
      <div className="legend">
        {hover !== null && <span className="muted">{xs[hover]}</span>}
        {series.map((s, k) => {
          const v = hover !== null ? s.points.find((p) => p.x === xs[hover])?.y : s.points.at(-1)?.y;
          return (
            <span key={s.name}>
              <i style={{ background: s.color ?? PALETTE[k % PALETTE.length] }} />
              {s.name}{v !== undefined && v !== null ? `: ${yFormat(v)}` : ""}
            </span>
          );
        })}
      </div>
    </div>
  );
}

export function Sparkline({ values, width = 120, height = 32 }: { values: number[]; width?: number; height?: number }) {
  if (values.length < 2) return null;
  const lo = Math.min(...values), hi = Math.max(...values) || lo + 1;
  const d = values.map((v, i) =>
    `${i ? "L" : "M"}${((i / (values.length - 1)) * width).toFixed(1)},${(height - 2 - ((v - lo) / (hi - lo || 1)) * (height - 4)).toFixed(1)}`).join("");
  const up = values[values.length - 1] >= values[0];
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
      <path d={d} fill="none" stroke={up ? "var(--pos)" : "var(--neg)"} strokeWidth={1.5} />
    </svg>
  );
}

/** Signed bars (e.g. monthly returns). */
export function BarChart({ data, height = 160, format = (v) => `${(v * 100).toFixed(1)}%`, ariaLabel }: {
  data: { label: string; value: number }[]; height?: number; format?: (v: number) => string;
  ariaLabel: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  if (!data.length) return <div className="state empty">No data to chart.</div>;
  const W = 800, H = height, T = 8, B = 20;
  const m = Math.max(...data.map((d) => Math.abs(d.value)), 1e-9);
  const zero = T + (H - T - B) / 2;
  const bw = (W - 8) / data.length;
  const every = Math.ceil(data.length / 8);
  return (
    <div className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={ariaLabel} preserveAspectRatio="none"
           onMouseLeave={() => setHover(null)}>
        <line x1={0} x2={W} y1={zero} y2={zero} className="grid" />
        {data.map((d, i) => {
          const h = (Math.abs(d.value) / m) * ((H - T - B) / 2);
          return (
            <g key={d.label} onMouseEnter={() => setHover(i)}>
              <rect x={4 + i * bw + bw * 0.12} width={Math.max(1, bw * 0.76)}
                    y={d.value >= 0 ? zero - h : zero} height={Math.max(0.5, h)}
                    fill={d.value >= 0 ? "var(--pos)" : "var(--neg)"} opacity={hover === i ? 1 : 0.8} />
              {i % every === 0 && <text x={4 + i * bw} y={H - 5} className="axis">{d.label}</text>}
            </g>
          );
        })}
      </svg>
      <div className="legend">
        {hover !== null ? <span>{data[hover].label}: {format(data[hover].value)}</span>
                        : <span className="muted">Hover a bar for its value</span>}
      </div>
    </div>
  );
}

/** Horizontal bars for exposures/weights. */
export function HBars({ data, format = (v) => `${(v * 100).toFixed(1)}%` }: {
  data: { label: string; value: number }[]; format?: (v: number) => string;
}) {
  if (!data.length) return <div className="state empty">Nothing to show.</div>;
  const m = Math.max(...data.map((d) => Math.abs(d.value)), 1e-9);
  return (
    <div className="hbars">
      {data.map((d) => (
        <div key={d.label} className="hbar">
          <span className="lbl" title={d.label}>{d.label}</span>
          <span className="track">
            <span className={d.value >= 0 ? "fill pos-bg" : "fill neg-bg"}
                  style={{ width: `${(Math.abs(d.value) / m) * 100}%` }} />
          </span>
          <span className="val">{format(d.value)}</span>
        </div>
      ))}
    </div>
  );
}
