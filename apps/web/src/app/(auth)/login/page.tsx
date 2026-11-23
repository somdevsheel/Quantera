"use client";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { Field, FormError } from "@/components/ui";
import { ApiError, auth, errorText } from "@/lib/api";

function safeNext(n: string | null): string {
  return n && n.startsWith("/") && !n.startsWith("//") ? n : "/";
}

function LoginForm() {
  const params = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [needCode, setNeedCode] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setError(undefined);
    try {
      await auth("login", { email, password, mfa_code: needCode ? code : undefined });
      location.assign(safeNext(params.get("next")));
    } catch (err) {
      if (err instanceof ApiError && err.code === "mfa_required") { setNeedCode(true); }
      else setError(errorText(err));
    } finally { setBusy(false); }
  };

  return (
    <form className="card form" onSubmit={submit}>
      <div><h1>Sign in</h1><p className="muted">Quantera research workspace</p></div>
      <FormError error={error} />
      <Field label="Email"><input type="email" autoComplete="username" required value={email}
                                  onChange={(e) => setEmail(e.target.value)} /></Field>
      <Field label="Password"><input type="password" autoComplete="current-password" required value={password}
                                     onChange={(e) => setPassword(e.target.value)} /></Field>
      {needCode && (
        <Field label="Authenticator code" hint="6-digit code from your app, or one of your recovery codes">
          <input autoFocus inputMode="text" autoComplete="one-time-code" required value={code}
                 onChange={(e) => setCode(e.target.value)} />
        </Field>
      )}
      <button className="btn primary" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
      <div className="row gap small">
        <Link href="/register">Create an account</Link><span className="muted">·</span>
        <Link href="/reset-password">Forgot password?</Link>
      </div>
    </form>
  );
}

export default function LoginPage() {
  return <Suspense><LoginForm /></Suspense>;
}
