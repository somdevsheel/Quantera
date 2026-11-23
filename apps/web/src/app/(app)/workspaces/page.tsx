"use client";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { Badge, Card, Field, FormError, Load, Notice, PageHead } from "@/components/ui";
import { api } from "@/lib/api";
import { date, dateTime } from "@/lib/fmt";
import { useAction, useApi } from "@/lib/hooks";

type Ws = { id: string; name: string; personal: boolean; role: string; current: boolean };
type Member = { user_id: string; email: string; name: string; role: string };
type Grant = { id: string; resource_type: string; resource_id: string; grantee_user_id: string | null;
  grantee_workspace_id: string | null; expires_at: string | null; revoked: boolean; url: string };

function Members({ ws }: { ws: Ws }) {
  const q = useApi<Member[]>(`workspaces/${ws.id}/members`);
  const act = useAction();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("MEMBER");
  const [token, setToken] = useState<{ email: string; token: string; expires_at: string }>();
  const admin = ws.role === "OWNER" || ws.role === "OPERATOR";
  return (
    <div className="stack">
      <FormError error={act.error} />
      <Load q={q}>
        {(d) => (
          <div className="table-wrap"><table><tbody>{d.map((m) => (
            <tr key={m.user_id}><td>{m.name}</td><td className="muted">{m.email}</td><td><Badge value={m.role} /></td>
              <td>{admin && m.role !== "OWNER" && <button className="btn small danger" disabled={act.busy}
                onClick={() => { if (confirm(`Remove ${m.email} from ${ws.name}?`)) void act.run(async () => { await api(`workspaces/${ws.id}/members/${m.user_id}`, { method: "DELETE" }); q.reload(); }); }}>Remove</button>}</td></tr>))}
          </tbody></table></div>
        )}
      </Load>
      {admin && !ws.personal && (
        <form className="row gap" onSubmit={async (e) => {
          e.preventDefault();
          const r = await act.run(() => api<{ email: string; token: string; expires_at: string }>(`workspaces/${ws.id}/invites`, { body: { email, role } }));
          if (r) { setToken(r); setEmail(""); }
        }}>
          <input type="email" required placeholder="Invite by email" value={email} onChange={(e) => setEmail(e.target.value)} />
          <select value={role} onChange={(e) => setRole(e.target.value)}><option value="MEMBER">Member</option><option value="GUEST">Guest (read-only)</option></select>
          <button className="btn">Invite</button>
        </form>
      )}
      {token && (
        <Notice tone="warn">
          No email service is configured. Send this one-time code to <b>{token.email}</b> yourself; it works only for that address and
          expires {dateTime(token.expires_at)}. They accept it under Workspaces → Accept invitation.<br />
          <code style={{ userSelect: "all", wordBreak: "break-all" }}>{token.token}</code>
        </Notice>
      )}
    </div>
  );
}

function Shares() {
  const params = useSearchParams();
  const mine = useApi<Grant[]>("shares");
  const ws = useApi<Ws[]>("workspaces");
  const act = useAction();
  const [f, setF] = useState({ resource_type: params.get("share") ?? "WATCHLIST", resource_id: params.get("id") ?? "",
                               to: "email", grantee_email: "", grantee_workspace_id: "", expires_in_days: "30" });
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  return (
    <Card title="Shares you created">
      <FormError error={act.error} />
      <form className="form" onSubmit={async (e) => {
        e.preventDefault();
        const r = await act.run(() => api("shares", { body: { resource_type: f.resource_type, resource_id: f.resource_id.trim(),
          grantee_email: f.to === "email" ? f.grantee_email : undefined, grantee_workspace_id: f.to === "ws" ? f.grantee_workspace_id : undefined,
          expires_in_days: f.expires_in_days ? Number(f.expires_in_days) : undefined } }));
        if (r) { mine.reload(); setF({ ...f, resource_id: "", grantee_email: "" }); }
      }}>
        <div className="form cols">
          <Field label="What"><select value={f.resource_type} onChange={set("resource_type")}><option value="WATCHLIST">Watchlist</option><option value="SCAN">Scan result</option><option value="BACKTEST">Backtest</option></select></Field>
          <Field label="Item id" hint="From the item's page or URL"><input required value={f.resource_id} onChange={set("resource_id")} /></Field>
          <Field label="With"><select value={f.to} onChange={set("to")}><option value="email">A person (email)</option><option value="ws">A group workspace</option></select></Field>
          {f.to === "email"
            ? <Field label="Email"><input type="email" required value={f.grantee_email} onChange={set("grantee_email")} /></Field>
            : <Field label="Workspace"><select required value={f.grantee_workspace_id} onChange={set("grantee_workspace_id")}><option value="">Choose…</option>
                {ws.data?.filter((w) => !w.personal).map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}</select></Field>}
          <Field label="Expires in (days)" hint="Blank = until revoked"><input type="number" min={1} max={365} value={f.expires_in_days} onChange={set("expires_in_days")} /></Field>
        </div>
        <button className="btn primary" disabled={act.busy}>Share read-only</button>
        <p className="muted small">Shares are read-only and can be revoked at any time. Portfolios cannot be shared.</p>
      </form>
      <Load q={mine} empty={(d) => d.length === 0 && "You haven't shared anything."}>
        {(d) => (
          <div className="table-wrap" style={{ marginTop: 10 }}><table>
            <thead><tr><th>Item</th><th>With</th><th>Expires</th><th>State</th><th /></tr></thead>
            <tbody>{d.map((g) => (
              <tr key={g.id}><td><Badge value={g.resource_type} /> <code className="small">{g.resource_id.slice(0, 8)}</code></td>
                <td className="small">{g.grantee_user_id ? "person" : "workspace"}</td><td>{g.expires_at ? date(g.expires_at) : "never"}</td>
                <td>{g.revoked ? <Badge value="REVOKED" /> : <Badge value="ACTIVE" />}</td>
                <td>{!g.revoked && <button className="btn small danger" disabled={act.busy} onClick={() => act.run(async () => { await api(`shares/${g.id}`, { method: "DELETE" }); mine.reload(); })}>Revoke</button>}</td></tr>))}
            </tbody>
          </table></div>
        )}
      </Load>
    </Card>
  );
}

function Inner() {
  const ws = useApi<Ws[]>("workspaces");
  const shared = useApi<Grant[]>("shared-with-me");
  const act = useAction();
  const [name, setName] = useState("");
  const [token, setToken] = useState("");
  const [joined, setJoined] = useState<string>();
  const [open, setOpen] = useState<string | null>(null);
  return (
    <>
      <PageHead title="Workspaces & sharing" sub="Your personal workspace is private. Group workspaces are invite-only; shares are explicit, read-only and revocable." />
      <FormError error={act.error} />
      <div className="grid g2">
        <Card title="Your workspaces">
          <Load q={ws}>
            {(d) => (
              <div className="table-wrap"><table><tbody>{d.map((w) => (
                <tr key={w.id}>
                  <td>{w.name} {w.personal && <span className="muted small">(personal)</span>}</td><td><Badge value={w.role} /></td>
                  <td>{w.current ? <Badge value="ACTIVE" title="current workspace" /> : (
                    <button className="btn small" disabled={act.busy} onClick={() => act.run(async () => { await api("auth/switch-workspace", { body: { workspace_id: w.id } }); location.assign("/"); })}>Switch</button>)}</td>
                  <td><button className="btn small" onClick={() => setOpen(open === w.id ? null : w.id)}>{open === w.id ? "Hide" : "Members"}</button></td>
                </tr>))}
              </tbody></table></div>
            )}
          </Load>
          {open && ws.data && <div style={{ marginTop: 10 }}><Members ws={ws.data.find((w) => w.id === open)!} /></div>}
          <form className="row gap" style={{ marginTop: 12 }} onSubmit={async (e) => { e.preventDefault(); if (await act.run(() => api("workspaces", { body: { name } }))) { setName(""); ws.reload(); } }}>
            <input required maxLength={120} placeholder="New group workspace" value={name} onChange={(e) => setName(e.target.value)} />
            <button className="btn">Create</button>
          </form>
        </Card>
        <Card title="Accept invitation">
          <form className="form" onSubmit={async (e) => {
            e.preventDefault();
            const r = await act.run(() => api<{ workspace_id: string; role: string }>("invites/accept", { body: { token: token.trim() } }));
            if (r) { setJoined(`Joined as ${r.role}.`); setToken(""); ws.reload(); }
          }}>
            <Field label="Invitation code"><input required minLength={20} value={token} onChange={(e) => setToken(e.target.value)} /></Field>
            <button className="btn">Accept</button>
            {joined && <Notice tone="good">{joined}</Notice>}
          </form>
          <h3>Shared with me</h3>
          <Load q={shared} empty={(d) => d.length === 0 && "Nothing is shared with you (in this workspace)."}>
            {(d) => (
              <ul className="notes" style={{ color: "inherit" }}>{d.map((g) => (
                <li key={g.id}><Link href={`/shared/${g.id}`}>{g.resource_type.toLowerCase()} {g.resource_id.slice(0, 8)}</Link> {g.expires_at && <span className="muted small">until {date(g.expires_at)}</span>}</li>))}
              </ul>
            )}
          </Load>
        </Card>
      </div>
      <div style={{ height: 14 }} />
      <Shares />
    </>
  );
}

export default function WorkspacesPage() {
  return <Suspense><Inner /></Suspense>;
}
