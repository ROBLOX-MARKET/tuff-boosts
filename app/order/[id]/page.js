"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";
import { STATUS_LABELS, orderLabel, shortDate, usd } from "@/lib/money";
import { discordUrl, useApp } from "@/components/Providers";

function CopyButton({ text, label = "Copy" }) {
  const [done, setDone] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const area = document.createElement("textarea");
      area.value = text;
      document.body.append(area);
      area.select();
      document.execCommand("copy");
      area.remove();
    }
    setDone(true);
    setTimeout(() => setDone(false), 1500);
  }
  return (
    <button className="small-button" type="button" onClick={copy} aria-live="polite">
      {done ? "Copied!" : label}
    </button>
  );
}

export default function OrderPage({ params }) {
  const orderId = params.id;
  const router = useRouter();
  const { session, authReady, site } = useApp();
  const [order, setOrder] = useState(null);
  const [codes, setCodes] = useState([]);
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    if (authReady && !session) router.replace("/login?next=/order/" + orderId);
  }, [authReady, session, router, orderId]);

  const load = useCallback(async () => {
    const [{ data: orderRow }, { data: codeRows }] = await Promise.all([
      supabase.from("orders").select("*").eq("id", orderId).maybeSingle(),
      supabase.from("product_codes").select("id, code").eq("order_id", orderId).eq("status", "delivered").order("id")
    ]);
    if (!orderRow) {
      setMissing(true);
      return null;
    }
    setOrder(orderRow);
    setCodes(codeRows || []);
    return orderRow;
  }, [orderId]);

  // Load, then keep checking every 10 seconds until the payment is confirmed.
  useEffect(() => {
    if (!session) return;
    let timer;
    let stopped = false;
    const tick = async () => {
      const current = await load();
      if (!stopped && current && current.status === "awaiting_payment") timer = setTimeout(tick, 10000);
    };
    tick();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [session, load]);

  if (missing) {
    return (
      <main className="wrap narrow page">
        <h1>Order not found</h1>
        <p className="page-lead">Make sure you&apos;re logged in to the account that placed this order.</p>
        <p><Link className="pill" href="/account">My orders</Link></p>
      </main>
    );
  }

  if (!order) {
    return <main className="wrap narrow page"><p className="page-lead">Loading your order…</p></main>;
  }

  const allCodes = codes.map((c) => c.code).join("\n");
  const waiting = order.status === "awaiting_payment";
  const paid = order.status === "paid" || order.status === "completed";

  return (
    <main className="wrap narrow page">
      <p className="page-lead" style={{ marginTop: 0 }}>Order {orderLabel(order)}</p>
      <h1>{waiting ? "Waiting for payment" : paid ? "Thanks for your order!" : STATUS_LABELS[order.status]}</h1>
      <p className="page-lead">
        {order.product_name}{order.quantity > 1 ? " × " + order.quantity : ""} · {usd(order.total_cents)} · {shortDate(order.created_at)}
      </p>

      {waiting && (
        <div className="card stack">
          <p style={{ margin: 0 }}>We&apos;ll show your order here as soon as your payment is confirmed on the blockchain. This page updates by itself.</p>
          {order.invoice_url && <a className="pill" href={order.invoice_url}>Go to payment page</a>}
        </div>
      )}

      {codes.length > 0 && (
        <div className="card">
          <div className="inline" style={{ alignItems: "center", justifyContent: "space-between", flexWrap: "wrap" }}>
            <h2>Your links ({codes.length})</h2>
            {codes.length > 1 && <CopyButton text={allCodes} label="Copy all" />}
          </div>
          <p className="hint">Copy each link and open it in Discord to redeem. We&apos;ve also emailed you a link to this page so you can come back anytime.</p>
          <div className="codes">
            {codes.map((c, i) => (
              <div className="code-row" key={c.id}>
                <span className="num">{i + 1}.</span>
                <code>{c.code}</code>
                <CopyButton text={c.code} />
              </div>
            ))}
          </div>
        </div>
      )}

      {paid && codes.length < order.quantity && order.quantity > 1 && codes.length > 0 && (
        <p className="notice" style={{ marginTop: 12 }}>The rest of your links are on their way. We&apos;ll add them here shortly.</p>
      )}

      {paid && codes.length === 0 && (
        <div className="card">
          <p style={{ margin: 0 }}>
            {order.server_link
              ? "Your payment is confirmed. We're sending your boosts to " + order.server_link + " now. We'll email you when it's done."
              : "Your payment is confirmed. Your items will appear here shortly."}
          </p>
        </div>
      )}

      <div className="row-actions" style={{ marginTop: 18 }}>
        <Link className="small-button ghost" href="/account">All my orders</Link>
        <a className="small-button ghost" href={discordUrl(site)} target="_blank" rel="noopener noreferrer">Need help? Discord</a>
      </div>
    </main>
  );
}
