"use client";

import Link from "next/link";
import { useState } from "react";
import { supabase } from "@/lib/supabase";

export default function ForgotPassword() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setError("");
    setBusy(true);
    const { error: resetError } = await supabase.auth.resetPasswordForEmail(email.trim(), {
      redirectTo: window.location.origin + "/reset-password"
    });
    setBusy(false);
    if (resetError) {
      setError("We couldn't send the email. Wait a minute and try again.");
      return;
    }
    setSent(true);
  }

  return (
    <main className="wrap narrow page">
      <h1>Reset your password</h1>
      {sent ? (
        <div className="card">
          <p className="notice">If an account uses {email}, we&apos;ve sent it a link to set a new password.</p>
          <p className="hint">Check your spam folder if it doesn&apos;t arrive in a few minutes.</p>
        </div>
      ) : (
        <form className="card stack" onSubmit={submit}>
          <p className="page-lead">Enter the email you signed up with and we&apos;ll send you a reset link.</p>
          <label>Email
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" required />
          </label>
          {error && <p className="error" role="alert">{error}</p>}
          <button className="pill" type="submit" disabled={busy}>{busy ? "Sending…" : "Send reset link"}</button>
        </form>
      )}
      <p className="auth-switch"><Link href="/login">Back to log in</Link></p>
    </main>
  );
}
