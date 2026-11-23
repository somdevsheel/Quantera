"use client";
// Shared building blocks, including the data states every screen must show honestly.
import Link from "next/link";
import type { ReactNode } from "react";

import { ApiError, errorText } from "@/lib/api";

const TONE: Record<string, string> = {
  OK: "good", FRESH: "good", SUCCEEDED: "good", FILLED: "good", ACTIVE: "good", BUY: "good",
  RECONCILED: "good", APPROVED: "good", NORMAL: "good", COMPLETE: "good", FEASIBLE: "good",
  OPTIMAL: "good", HOLD: "good",
  PARTIAL: "warn", STALE: "warn", WAIT: "warn", RUNNING: "info", QUEUED: "info",
  WORKING: "info", DRAFT: "info", RESEARCH: "info", CANDIDATE: "info", PAUSED: "warn",
  "TRIM REVIEW": "warn", "NO TRADE": "muted", PLANNED: "muted", PENDING: "muted",
  SUGGESTED: "info", CANCELLED: "muted", EXPIRED: "muted", RETIRED: "muted",
  UNSUPPORTED: "muted", "MODEL UNAVAILABLE": "muted", MISSING: "muted", DISABLED: "muted",
  DATA_BLOCKED: "warn", "NO_TRADE": "muted", NEW: "info", REVOKED: "muted", "READ ONLY": "info", UPCOMING: "info", OWNER: "info", MEMBER: "muted", GUEST: "warn", OPERATOR: "info", APPROVED_PAPER: "good", SCORED: "good", "DRIFT ALERT": "bad", "INSUFFICIENT DATA": "muted", "NO DATA AFTER AS_OF": "muted", PAPER: "info", LONG: "good", SHORT: "bad",
  FAILED: "bad", AVOID: "bad", REJECTED: "bad", INFEASIBLE: "bad", RISK_OFF: "bad",
  "EXIT REVIEW": "bad", BREAKS: "bad", TRIGGERED: "warn",
};

export function Badge({ value, title }: { value?: string | null; title?: string }) {
  if (!value) return <span className="badge muted">—</span>;
  return <span className={`badge ${TONE[value] ?? "muted"}`} title={title}>{value.replace(/_/g, " ")}</span>;
}

export function Card({ title, actions, children, className = "" }: {
  title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="card-head"><h2>{title}</h2><div className="row gap">{actions}</div></header>
      )}
      {children}
    </section>
  );
}

export function PageHead({ title, sub, actions }: { title: ReactNode; sub?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="page-head">
      <div><h1>{title}</h1>{sub && <p className="muted">{sub}</p>}</div>
      {actions && <div className="row gap">{actions}</div>}
    </div>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return <div className="state" role="status" aria-live="polite"><span className="spinner" />{label}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="state empty">{children}</div>;
}

export function ErrorBox({ error, retry }: { error: unknown; retry?: () => void }) {
  const offline = error instanceof ApiError && error.code === "offline";
  return (
    <div className={`state error ${offline ? "offline" : ""}`} role="alert">
      <strong>{offline ? "Offline" : error instanceof ApiError && error.status === 404 ? "Not found" : "Something went wrong"}</strong>
      <span>{errorText(error)}</span>
      {retry && <button className="btn small" onClick={retry}>Try again</button>}
    </div>
  );
}

export function Notice({ tone = "info", children }: { tone?: "info" | "warn" | "bad" | "good"; children: ReactNode }) {
  return <div className={`notice ${tone}`}>{children}</div>;
}

/** Render loading / error / empty, else children(data). */
export function Load<T>({ q, empty, children }: {
  q: { data?: T; error?: unknown; loading: boolean; reload: () => void };
  empty?: (d: T) => ReactNode | false; children: (d: T) => ReactNode;
}) {
  if (q.error && q.data === undefined) return <ErrorBox error={q.error} retry={q.reload} />;
  if (q.data === undefined) return <Loading />;
  const e = empty?.(q.data);
  return <>{q.error ? <ErrorBox error={q.error} retry={q.reload} /> : null}{e ? <Empty>{e}</Empty> : children(q.data)}</>;
}

export function StockLink({ route, symbol }: { route?: string; symbol: string }) {
  return <Link className="sym" href={route ?? `/stocks/XNSE/${encodeURIComponent(symbol)}`}>{symbol}</Link>;
}

export function KV({ items }: { items: [ReactNode, ReactNode][] }) {
  return (
    <dl className="kv">
      {items.map(([k, v], i) => (<div key={i}><dt>{k}</dt><dd>{v}</dd></div>))}
    </dl>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  // The hint sits outside the <label> so a field's accessible name is just its label.
  return (
    <div className="field">
      <label><span>{label}</span>{children}</label>
      {hint && <small className="muted">{hint}</small>}
    </div>
  );
}

export function FormError({ error }: { error?: string }) {
  return error ? <div className="form-error" role="alert">{error}</div> : null;
}

export function Notes({ notes }: { notes?: unknown }) {
  const list = Array.isArray(notes) ? (notes as unknown[]).filter((n) => typeof n === "string") as string[] : [];
  if (!list.length) return null;
  return <ul className="notes">{list.map((n, i) => <li key={i}>{n}</li>)}</ul>;
}

export function Progress({ value }: { value: number }) {
  const v = Math.max(0, Math.min(1, value));
  return <div className="progress" role="progressbar" aria-valuenow={Math.round(v * 100)} aria-valuemin={0} aria-valuemax={100}><div style={{ width: `${v * 100}%` }} /></div>;
}

export function Tabs<T extends string>({ tabs, value, onChange }: {
  tabs: readonly T[] | { id: T; label: string }[]; value: T; onChange: (t: T) => void;
}) {
  const list = (tabs as (T | { id: T; label: string })[]).map((t) => typeof t === "string" ? { id: t, label: t } : t);
  return (
    <div className="tabs" role="tablist">
      {list.map((t) => (
        <button key={t.id} role="tab" aria-selected={value === t.id} className={value === t.id ? "on" : ""}
                onClick={() => onChange(t.id)}>{t.label}</button>
      ))}
    </div>
  );
}
