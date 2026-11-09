"use client";

import Link from "next/link";
import Logo from "./Logo";
import { discordUrl, useApp } from "./Providers";

export default function Header() {
  const { user, isAdmin, site } = useApp();
  return (
    <header className="topbar">
      <div className="wrap">
        <Link className="brand" href="/">
          <Logo />
          Tuff Boosts
        </Link>
        <nav className="nav" aria-label="Main">
          <Link href="/#store">Store</Link>
          <Link href="/#how">How it works</Link>
          <Link href="/#faq">FAQ</Link>
          <a href={discordUrl(site)} target="_blank" rel="noopener noreferrer">Discord</a>
          {isAdmin && <Link href="/admin">Admin</Link>}
          {user ? (
            <Link className="pill" href="/account">My account</Link>
          ) : (
            <>
              <Link href="/login">Log in</Link>
              <Link className="pill" href="/signup">Sign up</Link>
            </>
          )}
        </nav>
        <Link className="small-button account-chip" href={user ? "/account" : "/login"}>
          {user ? "Account" : "Log in"}
        </Link>
      </div>
    </header>
  );
}
