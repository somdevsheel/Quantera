"use client";
import Link from "next/link";
import { Fragment, useState } from "react";

import { Badge, Card, Field, FormError, Load, Notes, PageHead, StockLink } from "@/components/ui";
import { api } from "@/lib/api";
import { date, dateTime } from "@/lib/fmt";
import { useAction, useApi } from "@/lib/hooks";
import type { Portfolio, Universe } from "@/lib/types";

type R = Record<string, any>;
type Catalog = { indices: { index: string; latest_members: number; snapshots: string[] }[]; industries: { industry: string; members: number }[]; notes: string[] };
type Watchlist = { id: string; name: string; created_at: string; items?: { id: string; symbol: string; exchange: string; name: string | null }[] | null };

function NewUniverse({ onDone }: { onDone: () => void }) {
  const cat = useApi<Catalog>("universes/catalog");
  const wls = useApi<Watchlist[]>("watchlists");
  const pfs = useApi<Portfolio[]>("portfolios");
  const act = useAction();
  const [f, setF] = useState({ name: "", kind: "INDEX", index: "NIFTY 50", industry: "", symbols: "", watchlist_id: "", portfolio_id: "" });
  const [file, setFile] = useState<File | null>(null);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => setF({ ...f, [k]: e.target.value });
  return (
    <Card title="New universe">
      <form className="form" onSubmit={async (e) => {
        e.preventDefault();
        const body: R = { name: f.name, kind: f.kind };
        if (f.kind === "INDEX") body.index = f.index;
        if (f.kind === "SECTOR") body.industry = f.industry;
        if (f.kind === "MANUAL") body.symbols = f.symbols.split(/[\s,]+/).map((s) => s.trim().toUpperCase()).filter(Boolean);
        if (f.kind === "WATCHLIST") body.watchlist_id = f.watchlist_id;
        if (f.kind === "HOLDINGS") body.portfolio_id = f.portfolio_id;
        const r = f.kind === "CSV"
          ? await act.run(() => { const fd = new FormData(); fd.append("name", f.name); fd.append("file", file as File); return api("universes/import-csv", { form: fd }); })
          : await act.run(() => api("universes", { body }));
        if (r) { setF({ ...f, name: "" }); onDone(); }
      }}>
        <FormError error={act.error} />
        <div className="form cols">
          <Field label="Name"><input required maxLength={120} value={f.name} onChange={set("name")} /></Field>
          <Field label="Kind"><select value={f.kind} onChange={set("kind")}>
            {["INDEX", "SECTOR", "MANUAL", "WATCHLIST", "HOLDINGS", "CSV"].map((k) => <option key={k}>{k}</option>)}</select></Field>
          {f.kind === "INDEX" && <Field label="Index"><select value={f.index} onChange={set("index")}>{cat.data?.indices.map((i) => <option key={i.index}>{i.index}</option>)}</select></Field>}
          {f.kind === "SECTOR" && <Field label="Industry"><select required value={f.industry} onChange={set("industry")}><option value="">Choose…</option>{cat.data?.industries.map((i) => <option key={i.industry}>{i.industry}</option>)}</select></Field>}
          {f.kind === "WATCHLIST" && <Field label="Watchlist"><select required value={f.watchlist_id} onChange={set("watchlist_id")}><option value="">Choose…</option>{wls.data?.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}</select></Field>}
          {f.kind === "HOLDINGS" && <Field label="Portfolio"><select required value={f.portfolio_id} onChange={set("portfolio_id")}><option value="">Choose…</option>{pfs.data?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></Field>}
        </div>
        {f.kind === "MANUAL" && <Field label="Symbols (NSE)" hint="Comma or space separated"><textarea rows={3} required value={f.symbols} onChange={set("symbols")} /></Field>}
        {f.kind === "CSV" && <Field label="CSV file" hint="A `symbol` column (optional `exchange`). Max 5 MB; formulas are rejected."><input type="file" accept=".csv,text/csv" required onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></Field>}
        <button className="btn primary" disabled={act.busy}>Create</button>
        {cat.data && <Notes notes={cat.data.notes} />}
      </form>
    </Card>
  );
}

function UniverseRow({ u, onChange }: { u: Universe; onChange: () => void }) {
  const [open, setOpen] = useState(false);
  const snaps = useApi<R[]>(open ? `universes/${u.id}/snapshots` : null);
  const act = useAction();
  const [snap, setSnap] = useState<R>();
  return (
    <Fragment>
      <tr>
        <td>{u.name}</td><td><Badge value={u.kind} /></td><td className="small muted">{Object.values(u.params ?? {}).filter((v) => typeof v === "string").join(", ")}</td>
        <td className="muted small">{dateTime(u.created_at)}</td>
        <td><div className="row gap">
          <button className="btn small" onClick={() => setOpen(!open)}>{open ? "Hide" : "Snapshots"}</button>
          <button className="btn small danger" disabled={act.busy} onClick={() => { if (confirm(`Delete universe "${u.name}"?`)) void act.run(async () => { await api(`universes/${u.id}`, { method: "DELETE" }); onChange(); }); }}>Delete</button>
        </div></td>
      </tr>
      {open && (
        <tr><td colSpan={5} className="wrap">
          <FormError error={act.error} />
          <div className="row gap" style={{ marginBottom: 8 }}>
            <button className="btn small" disabled={act.busy} onClick={() => act.run(async () => { await api(`universes/${u.id}/snapshots`, { body: {} }); snaps.reload(); })}>Take snapshot now</button>
            <span className="muted small">A snapshot freezes membership so scans are reproducible.</span>
          </div>
          <Load q={snaps} empty={(d) => d.length === 0 && "No snapshots yet."}>
            {(d) => (
              <ul className="notes" style={{ color: "inherit" }}>
                {d.map((s) => (
                  <li key={s.id}>
                    <a href="#" onClick={async (e) => { e.preventDefault(); setSnap(await api<R>(`universe-snapshots/${s.id}`)); }}>{dateTime(s.created_at)}</a>
                    {" "}· data {date(s.source_snapshot_date ?? s.requested_as_of)} · {s.member_count} members · <code className="small">{String(s.content_hash).slice(0, 10)}</code>
                    {(s.unresolved ?? []).length > 0 && <> · <Badge value="PARTIAL" title={`${s.unresolved.length} unresolved`} /></>}
                  </li>))}
              </ul>
            )}
          </Load>
          {snap && (
            <div style={{ marginTop: 8 }}>
              <Notes notes={snap.notes} />
              <div className="row gap">{(snap.members ?? []).slice(0, 500).map((m: R) => <StockLink key={m.instrument_id ?? m.symbol} symbol={m.symbol} route={m.route} />)}</div>
            </div>
          )}
        </td></tr>
      )}
    </Fragment>
  );
}

function Watchlists() {
  const wls = useApi<Watchlist[]>("watchlists");
  const act = useAction();
  const [name, setName] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const detail = useApi<Watchlist>(open ? `watchlists/${open}` : null);
  const [sym, setSym] = useState("");
  return (
    <Card title="Watchlists">
      <FormError error={act.error} />
      <form className="row gap" onSubmit={async (e) => { e.preventDefault(); if (await act.run(() => api("watchlists", { body: { name } }))) { setName(""); wls.reload(); } }}>
        <input required placeholder="New watchlist name" maxLength={120} value={name} onChange={(e) => setName(e.target.value)} />
        <button className="btn">Create</button>
      </form>
      <Load q={wls} empty={(d) => d.length === 0 && "No watchlists yet."}>
        {(d) => (
          <div className="table-wrap" style={{ marginTop: 10 }}><table><tbody>{d.map((w) => (
            <Fragment key={w.id}>
              <tr><td>{w.name}</td><td className="muted small">{dateTime(w.created_at)}</td>
                <td><div className="row gap">
                  <button className="btn small" onClick={() => setOpen(open === w.id ? null : w.id)}>{open === w.id ? "Hide" : "Open"}</button>
                  <Link className="btn small" href={`/workspaces?share=WATCHLIST&id=${w.id}`}>Share</Link>
                  <button className="btn small danger" onClick={() => { if (confirm(`Delete watchlist "${w.name}"?`)) void act.run(async () => { await api(`watchlists/${w.id}`, { method: "DELETE" }); wls.reload(); }); }}>Delete</button>
                </div></td></tr>
              {open === w.id && (
                <tr><td colSpan={3} className="wrap">
                  <form className="row gap" onSubmit={async (e) => { e.preventDefault(); if (await act.run(() => api(`watchlists/${w.id}/items`, { body: { symbol: sym.trim().toUpperCase() } }))) { setSym(""); detail.reload(); } }}>
                    <input required placeholder="Add symbol (NSE)" value={sym} onChange={(e) => setSym(e.target.value)} /><button className="btn small">Add</button>
                  </form>
                  <Load q={detail} empty={(x) => !(x.items ?? []).length && "Empty."}>
                    {(x) => (
                      <div className="row gap" style={{ marginTop: 8 }}>
                        {(x.items ?? []).map((i) => (
                          <span key={i.id} className="badge muted"><StockLink symbol={i.symbol} route={`/stocks/${i.exchange}/${i.symbol}`} />{" "}
                            <a href="#" aria-label={`Remove ${i.symbol}`} onClick={(e) => { e.preventDefault(); void act.run(async () => { await api(`watchlists/${w.id}/items/${i.id}`, { method: "DELETE" }); detail.reload(); }); }}>×</a></span>))}
                      </div>
                    )}
                  </Load>
                </td></tr>
              )}
            </Fragment>))}
          </tbody></table></div>
        )}
      </Load>
    </Card>
  );
}

export default function UniversesPage() {
  const unis = useApi<Universe[]>("universes");
  return (
    <>
      <PageHead title="Universes & watchlists" sub="Universes define what the scanner, builder and backtests look at. Membership is snapshotted so results can be reproduced." />
      <div className="grid g2">
        <NewUniverse onDone={unis.reload} />
        <Watchlists />
      </div>
      <div style={{ height: 14 }} />
      <Card title="Universes">
        <Load q={unis} empty={(d) => d.length === 0 && "No universes yet."}>
          {(d) => (
            <div className="table-wrap"><table>
              <thead><tr><th>Name</th><th>Kind</th><th>Source</th><th>Created</th><th /></tr></thead>
              <tbody>{d.map((u) => <UniverseRow key={u.id} u={u} onChange={unis.reload} />)}</tbody>
            </table></div>
          )}
        </Load>
      </Card>
    </>
  );
}
