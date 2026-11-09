"use client";

import Link from "next/link";
import { discordUrl, useApp } from "./Providers";

export default function Footer() {
  const { site, user, isAdmin } = useApp();
  const discord = discordUrl(site);
  return (
    <>
      <footer>
        <div className="wrap footer-grid">
          <span>Tuff Boosts</span>
          <div className="footer-links">
            <Link href="/#store">Store</Link>
            <Link href="/#faq">FAQ</Link>
            <Link href="/terms">Terms of Service</Link>
            <Link href="/privacy">Privacy Policy</Link>
            {isAdmin && <Link href="/admin">Admin</Link>}
            <a href={discord} target="_blank" rel="noopener noreferrer">Discord</a>
          </div>
        </div>
      </footer>
      <nav className="tabbar" aria-label="Quick links">
        <Link href="/#store">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M3 9h18l-2 11H5zM8 9l4-6 4 6" /></svg>
          Store
        </Link>
        <Link href={user ? "/account" : "/login"}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><circle cx="12" cy="8" r="4" /><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6" /></svg>
          Account
        </Link>
        <a href={discord} target="_blank" rel="noopener noreferrer">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M4 5h16v12H9l-5 4z" /></svg>
          Discord
        </a>
        <Link href="/#faq">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.7.3-1 .8-1 1.7M12 17h.01" /></svg>
          FAQ
        </Link>
      </nav>
    </>
  );
}
