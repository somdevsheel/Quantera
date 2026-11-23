"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { api, auth } from "@/lib/api";
import { useApi, useOnline } from "@/lib/hooks";

type Me = { id: string; email: string; full_name: string; mfa_enabled: boolean;
            workspace: { id: string; name: string }; role: string };
type Ws = { id: string; name: string; personal: boolean; role: string; current: boolean };
type Hit = { instrument_id: string; exchange_mic: string; listing_symbol: string;
             listing_name: string | null; route: string };

const NAV: { group: string; items: [string, string][] }[] = [
  { group: "Markets", items: [["/", "Dashboard"], ["/scanner", "Scanner"], ["/universes", "Universes & watchlists"]] },
  { group: "Portfolios", items: [["/portfolios", "Portfolios & risk"], ["/builder", "Market-neutral builder"],
                                 ["/plans", "Trade plans"], ["/paper", "Paper trading"]] },
  { group: "Research", items: [["/strategies", "Strategies"], ["/backtests", "Backtests"], ["/models", "Models"]] },
  { group: "Workspace", items: [["/alerts", "Alerts"], ["/workspaces", "Workspaces & sharing"], ["/settings", "Settings"]] },
];

function Search() {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Hit[]>([]);
  const [open, setOpen] = useState(false);
  const [sel, setSel] = useState(0);
  const [err, setErr] = useState<string>();
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (q.trim().length < 2) { setHits([]); return; }
    const t = setTimeout(() => {
      api<Hit[]>("stocks/search", { query: { q: q.trim() } })
        .then((h) => { setHits(h); setErr(undefined); setSel(0); })
        .catch(() => setErr("Search is unavailable"));
    }, 220);
    return () => clearTimeout(t);
  }, [q]);
  useEffect(() => {
    const close = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  const go = (h: Hit) => { setOpen(false); setQ(""); router.push(h.route); };
  return (
    <div className="search" ref={box}>
      <input type="search" placeholder="Search stocks (symbol or name)…" value={q} aria-label="Search stocks"
             onChange={(e) => { setQ(e.target.value); setOpen(true); }} onFocus={() => setOpen(true)}
             onKeyDown={(e) => {
               if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(s + 1, hits.length - 1)); }
               if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(s - 1, 0)); }
               if (e.key === "Enter" && hits[sel]) go(hits[sel]);
               if (e.key === "Escape") setOpen(false);
             }} />
      {open && q.trim().length >= 2 && (
        <ul role="listbox">
          {err && <li><span className="muted">{err}</span></li>}
          {!err && hits.length === 0 && <li><span className="muted">No matches</span></li>}
          {hits.map((h, i) => (
            <li key={h.route}>
              <a href={h.route} className={i === sel ? "on" : ""} onClick={(e) => { e.preventDefault(); go(h); }}>
                <b>{h.listing_symbol}</b>
                <span className="muted small">{h.listing_name ?? ""} · {h.exchange_mic === "XNSE" ? "NSE" : "BSE"}</span>
              </a>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function WorkspaceSwitch({ me }: { me?: Me }) {
  const ws = useApi<Ws[]>("workspaces");
  const [busy, setBusy] = useState(false);
  if (!ws.data || ws.data.length < 2) return me ? <span className="muted small">{me.workspace.name}</span> : null;
  return (
    <select aria-label="Workspace" value={ws.data.find((w) => w.current)?.id} disabled={busy}
            onChange={async (e) => {
              setBusy(true);
              try { await api("auth/switch-workspace", { body: { workspace_id: e.target.value } }); location.assign("/"); }
              finally { setBusy(false); }
            }}>
      {ws.data.map((w) => <option key={w.id} value={w.id}>{w.name}{w.role === "GUEST" ? " (read-only)" : ""}</option>)}
    </select>
  );
}

export default function Shell({ children }: { children: ReactNode }) {
  const path = usePathname();
  const me = useApi<Me>("auth/me");
  const online = useOnline();
  const [open, setOpen] = useState(false);
  useEffect(() => setOpen(false), [path]);
  const active = (href: string) => (href === "/" ? path === "/" : path.startsWith(href));
  return (
    <div className="shell">
      <aside className={`side ${open ? "open" : ""}`} aria-label="Main navigation">
        <div className="brand">Quantera<small>Indian equities · research & paper trading</small></div>
        {NAV.map((g) => (
          <div className="nav-group nav" key={g.group}>
            <span>{g.group}</span>
            {g.items.map(([href, label]) => (
              <Link key={href} href={href} className={active(href) ? "on" : ""}>{label}</Link>
            ))}
          </div>
        ))}
      </aside>
      <div className="main">
        <div className="top">
          <button className="btn menu-btn" aria-label="Menu" onClick={() => setOpen((o) => !o)}>☰</button>
          <Search />
          <div className="grow" />
          <WorkspaceSwitch me={me.data} />
          {me.data?.role === "GUEST" && <span className="badge warn">Read-only</span>}
          <Link href="/settings" className="muted small" title={me.data?.email}>{me.data?.full_name ?? ""}</Link>
          <button className="btn small" onClick={async () => { await auth("logout"); location.assign("/login"); }}>
            Sign out
          </button>
        </div>
        {!online && <div className="banner" role="status">You are offline. Figures shown may be out of date; changes will fail until you reconnect.</div>}
        <main className="content">{children}</main>
      </div>
    </div>
  );
}
