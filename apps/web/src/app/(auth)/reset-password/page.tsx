"use client";
import Link from "next/link";
import { useState } from "react";

import { Field, FormError, Notice } from "@/components/ui";
import { ApiError, errorText, request } from "@/lib/api";

// Reset requests go straight to the API through the proxy without a session.
const call = <T,>(path: string, body: unknown) => request<T>("/bff/api", path, { method: "POST", body, anon: true });

export default function ResetPage() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState<string>();
  const [token, setToken] = useState("");
  const [pw, setPw] = useState("");
  const [code, setCode] = useState("");
  const [needCode, setNeedCode] = useState(false);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  if (done) {
    return (
      <div className="card form">
        <h1>Password changed</h1>
        <p>Every session of the account was signed out. Sign in with the new password.</p>
        <Link className="btn primary" href="/login">Sign in</Link>
      </div>
    );
  }
  return (
    <div className="card form">
      <div><h1>Reset password</h1><p className="muted">We never say whether an email is registered.</p></div>
      <FormError error={error} />
      {!sent ? (
        <form className="form" onSubmit={async (e) => {
          e.preventDefault(); setBusy(true); setError(undefined);
          try {
            const r = await call<{ message: string; dev_token?: string }>("auth/password-reset/request", { email });
            setSent(r.message);
            if (r.dev_token) setToken(r.dev_token);
          } catch (err) { setError(errorText(err)); } finally { setBusy(false); }
        }}>
          <Field label="Email"><input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
          <button className="btn primary" disabled={busy}>Send reset link</button>
        </form>
      ) : (
        <form className="form" onSubmit={async (e) => {
          e.preventDefault(); setBusy(true); setError(undefined);
          try {
            await call("auth/password-reset/confirm", { token, new_password: pw, mfa_code: needCode ? code : undefined });
            setDone(true);
          } catch (err) {
            if (err instanceof ApiError && err.code === "mfa_required") setNeedCode(true);
            else setError(errorText(err));
          } finally { setBusy(false); }
        }}>
          <Notice>{sent}</Notice>
          {token && <Notice tone="warn">Development server: no mail service is configured, so the reset token was filled in for you.</Notice>}
          <Field label="Reset token"><input required value={token} onChange={(e) => setToken(e.target.value)} /></Field>
          <Field label="New password" hint="At least 12 characters">
            <input type="password" autoComplete="new-password" required minLength={12} value={pw} onChange={(e) => setPw(e.target.value)} />
          </Field>
          {needCode && <Field label="Authenticator or recovery code"><input required value={code} onChange={(e) => setCode(e.target.value)} /></Field>}
          <button className="btn primary" disabled={busy}>Set new password</button>
        </form>
      )}
      <div className="small"><Link href="/login">Back to sign in</Link></div>
    </div>
  );
}
