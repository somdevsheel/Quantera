"use client";
import Link from "next/link";
import { useState } from "react";

import { Field, FormError } from "@/components/ui";
import { auth, errorText } from "@/lib/api";

export default function RegisterPage() {
  const [f, setF] = useState({ full_name: "", email: "", password: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
  return (
    <form className="card form" onSubmit={async (e) => {
      e.preventDefault(); setBusy(true); setError(undefined);
      try { await auth("register", f); location.assign("/"); }
      catch (err) { setError(errorText(err)); }
      finally { setBusy(false); }
    }}>
      <div><h1>Create account</h1><p className="muted">You get a private workspace. Nothing is shared unless you share it.</p></div>
      <FormError error={error} />
      <Field label="Full name"><input required maxLength={120} value={f.full_name} onChange={set("full_name")} /></Field>
      <Field label="Email"><input type="email" autoComplete="username" required value={f.email} onChange={set("email")} /></Field>
      <Field label="Password" hint="At least 12 characters; must not contain your email name.">
        <input type="password" autoComplete="new-password" required minLength={12} value={f.password} onChange={set("password")} />
      </Field>
      <button className="btn primary" disabled={busy}>{busy ? "Creating…" : "Create account"}</button>
      <div className="small"><Link href="/login">Already have an account? Sign in</Link></div>
    </form>
  );
}
