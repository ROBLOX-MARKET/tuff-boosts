"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { supabase } from "@/lib/supabase";
import { useApp } from "@/components/Providers";

export default function ResetPassword() {
  const router = useRouter();
  const { session, authReady } = useApp();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setError("");
    if (password.length < 8) {
      setError("Use a password of at least 8 characters.");
      return;
    }
    if (password !== confirm) {
      setError("The two passwords don't match.");
      return;
    }
    setBusy(true);
    const { error: updateError } = await supabase.auth.updateUser({ password });
    setBusy(false);
    if (updateError) {
      setError(updateError.message);
      return;
    }
    router.push("/account?reset=1");
  }

  if (!authReady) {
    return <main className="wrap narrow page"><p className="page-lead">Loading…</p></main>;
  }

  if (!session) {
    return (
      <main className="wrap narrow page">
        <h1>Link expired</h1>
        <p className="page-lead">This reset link has expired or was already used.</p>
        <p><Link className="pill" href="/forgot-password">Send a new link</Link></p>
      </main>
    );
  }

  return (
    <main className="wrap narrow page">
      <h1>Choose a new password</h1>
      <form className="card stack" onSubmit={submit}>
        <label>New password
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" minLength={8} required />
        </label>
        <label>Type it again
          <input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" minLength={8} required />
        </label>
        {error && <p className="error" role="alert">{error}</p>}
        <button className="pill" type="submit" disabled={busy}>{busy ? "Saving…" : "Save new password"}</button>
      </form>
    </main>
  );
}
