"use client";
import { useState } from "react";

import { Badge, Card, Field, FormError, KV, Load, Notice, PageHead } from "@/components/ui";
import { api } from "@/lib/api";
import { dateTime } from "@/lib/fmt";
import { useAction, useApi } from "@/lib/hooks";

type Me = { id: string; email: string; full_name: string; mfa_enabled: boolean; workspace: { name: string }; role: string };
type Sess = { id: string; created_at: string; last_used_at: string | null; expires_at: string; user_agent: string | null; current: boolean };

function Mfa() {
  const st = useApi<{ mfa_enabled: boolean; recovery_codes_left: number }>("auth/mfa");
  const act = useAction();
  const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string }>();
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState<string[]>();
  const [pw, setPw] = useState("");
  return (
    <Card title="Two-factor authentication">
      <FormError error={act.error} />
      <Load q={st}>
        {(s) => s.mfa_enabled ? (
          <div className="stack">
            <p><Badge value="ACTIVE" /> Sign-in needs a code from your authenticator app. {s.recovery_codes_left} recovery code(s) left.</p>
            {codes && <RecoveryCodes codes={codes} />}
            <form className="form" onSubmit={async (e) => {
              e.preventDefault();
              if (await act.run(() => api("auth/mfa/disable", { body: { password: pw, code } })) !== undefined) { setPw(""); setCode(""); st.reload(); }
            }}>
              <h3>Turn off</h3>
              <div className="form cols">
                <Field label="Password"><input type="password" required autoComplete="current-password" value={pw} onChange={(e) => setPw(e.target.value)} /></Field>
                <Field label="Code or recovery code"><input required value={code} onChange={(e) => setCode(e.target.value)} /></Field>
              </div>
              <button className="btn danger" disabled={act.busy}>Disable two-factor</button>
            </form>
          </div>
        ) : !setup ? (
          <div className="stack">
            <p>Protect your account with a time-based code (Google Authenticator, Aegis, 1Password…).</p>
            <button className="btn primary" disabled={act.busy} onClick={() => act.run(async () => setSetup(await api("auth/mfa/setup", { method: "POST" })))}>Set up</button>
          </div>
        ) : (
          <form className="form" onSubmit={async (e) => {
            e.preventDefault();
            const r = await act.run(() => api<{ recovery_codes: string[] }>("auth/mfa/enable", { body: { code } }));
            if (r) { setCodes(r.recovery_codes); setSetup(undefined); setCode(""); st.reload(); }
          }}>
            <p className="small">Add this account to your authenticator app by entering the secret key below (or opening the link on the device that has the app), then type the 6-digit code it shows.</p>
            <KV items={[["Secret key", <code key="k" style={{ userSelect: "all", wordBreak: "break-all" }}>{setup.secret}</code>]]} />
            <p className="small"><a href={setup.otpauth_uri}>Open in authenticator app</a></p>
            <Field label="6-digit code"><input required inputMode="numeric" autoComplete="one-time-code" pattern="[0-9 ]{6,7}" value={code} onChange={(e) => setCode(e.target.value)} /></Field>
            <button className="btn primary" disabled={act.busy}>Turn on</button>
          </form>
        )}
      </Load>
      {codes && !st.data?.mfa_enabled && <RecoveryCodes codes={codes} />}
    </Card>
  );
}

function RecoveryCodes({ codes }: { codes: string[] }) {
  return (
    <Notice tone="warn">
      <b>Save these recovery codes now.</b> Each works once if you lose your device; they will not be shown again.
      <pre className="mono" style={{ userSelect: "all", columns: 2, margin: "8px 0 0" }}>{codes.join("\n")}</pre>
    </Notice>
  );
}

function Password() {
  const act = useAction();
  const [f, setF] = useState({ current_password: "", new_password: "" });
  return (
    <Card title="Change password">
      <form className="form" onSubmit={async (e) => {
        e.preventDefault();
        if (await act.run(() => api("auth/password", { body: f }))) location.assign("/login");
      }}>
        <FormError error={act.error} />
        <Field label="Current password"><input type="password" required autoComplete="current-password" value={f.current_password} onChange={(e) => setF({ ...f, current_password: e.target.value })} /></Field>
        <Field label="New password" hint="At least 12 characters. All sessions, including this one, will be signed out.">
          <input type="password" required minLength={12} autoComplete="new-password" value={f.new_password} onChange={(e) => setF({ ...f, new_password: e.target.value })} /></Field>
        <button className="btn" disabled={act.busy}>Change password</button>
      </form>
    </Card>
  );
}

function Sessions() {
  const q = useApi<Sess[]>("auth/sessions");
  const act = useAction();
  return (
    <Card title="Signed-in sessions">
      <FormError error={act.error} />
      <Load q={q}>
        {(d) => (
          <div className="table-wrap"><table>
            <thead><tr><th>Device</th><th>Signed in</th><th>Last used</th><th>Expires</th><th /></tr></thead>
            <tbody>{d.map((s) => (
              <tr key={s.id}><td className="small wrap">{s.user_agent ?? "unknown"} {s.current && <Badge value="ACTIVE" title="this session" />}</td>
                <td className="small">{dateTime(s.created_at)}</td><td className="small">{dateTime(s.last_used_at)}</td><td className="small">{dateTime(s.expires_at)}</td>
                <td>{!s.current && <button className="btn small danger" disabled={act.busy} onClick={() => act.run(async () => { await api(`auth/sessions/${s.id}`, { method: "DELETE" }); q.reload(); })}>Sign out</button>}</td></tr>))}
            </tbody>
          </table></div>
        )}
      </Load>
    </Card>
  );
}

export default function SettingsPage() {
  const me = useApi<Me>("auth/me");
  return (
    <>
      <PageHead title="Settings" />
      <Load q={me}>{(m) => (
        <Card title="Account"><KV items={[["Name", m.full_name], ["Email", m.email], ["Workspace", m.workspace.name], ["Role", m.role]]} /></Card>
      )}</Load>
      <div className="grid g2" style={{ marginTop: 14 }}>
        <Mfa />
        <Password />
      </div>
      <div style={{ height: 14 }} />
      <Sessions />
      <p className="muted small" style={{ marginTop: 14 }}>Live trading is disabled on this platform. Orders exist only in paper portfolios.</p>
    </>
  );
}
