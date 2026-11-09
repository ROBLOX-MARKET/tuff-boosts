"use client";

import Link from "next/link";
import { useState } from "react";
import { supabase } from "@/lib/supabase";

export default function SignUp() {
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setError("");
    const name = username.trim();
    if (!/^[A-Za-z0-9_]{3,20}$/.test(name)) {
      setError("Usernames are 3 to 20 letters, numbers or underscores.");
      return;
    }
    if (password.length < 8) {
      setError("Use a password of at least 8 characters.");
      return;
    }
    setBusy(true);
    const { data: free } = await supabase.rpc("username_available", { p_username: name });
    if (free === false) {
      setBusy(false);
      setError("That username is taken. Try another.");
      return;
    }
    const { error: signUpError } = await supabase.auth.signUp({
      email: email.trim(),
      password,
      options: { data: { username: name }, emailRedirectTo: window.location.origin + "/account" }
    });
    setBusy(false);
    if (signUpError) {
      setError(signUpError.message.includes("Database error") ? "That username is taken. Try another." : signUpError.message);
      return;
    }
    setDone(true);
  }

  if (done) {
    return (
      <main className="wrap narrow page">
        <h1>Check your email</h1>
        <p className="page-lead">We sent a link to {email}. Open it to confirm your account, then you can log in.</p>
      </main>
    );
  }

  return (
    <main className="wrap narrow page">
      <h1>Create an account</h1>
      <p className="page-lead">Track your orders, keep your credit, and earn $2 for every $20 you spend.</p>
      <form className="card stack" onSubmit={submit}>
        <label>Username
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" maxLength={20} required />
          <span className="hint">3 to 20 letters, numbers or underscores.</span>
        </label>
        <label>Email
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" required />
        </label>
        <label>Password
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" minLength={8} required />
          <span className="hint">At least 8 characters.</span>
        </label>
        {error && <p className="error" role="alert">{error}</p>}
        <button className="pill" type="submit" disabled={busy}>{busy ? "Creating account…" : "Sign up"}</button>
      </form>
      <p className="auth-switch">Already have an account? <Link href="/login">Log in</Link></p>
    </main>
  );
}
