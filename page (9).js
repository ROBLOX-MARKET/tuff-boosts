"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { supabase } from "@/lib/supabase";

function safeNext() {
  if (typeof window === "undefined") return "/account";
  const next = new URLSearchParams(window.location.search).get("next") || "/account";
  return next.startsWith("/") && !next.startsWith("//") ? next : "/account";
}

export default function Login() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setError("");
    setBusy(true);
    const { error: loginError } = await supabase.auth.signInWithPassword({ email: email.trim(), password });
    setBusy(false);
    if (loginError) {
      setError(loginError.message === "Invalid login credentials" ? "That email or password is wrong." : loginError.message);
      return;
    }
    router.push(safeNext());
  }

  return (
    <main className="wrap narrow page">
      <h1>Log in</h1>
      <form className="card stack" onSubmit={submit}>
        <label>Email
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" required />
        </label>
        <label>Password
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
        </label>
        {error && <p className="error" role="alert">{error}</p>}
        <button className="pill" type="submit" disabled={busy}>{busy ? "Logging in…" : "Log in"}</button>
        <Link href="/forgot-password">Forgot password?</Link>
      </form>
      <p className="auth-switch">New here? <Link href="/signup">Create an account</Link></p>
    </main>
  );
}
