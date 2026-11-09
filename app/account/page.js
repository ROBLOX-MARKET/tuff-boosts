"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";
import { REWARD_CENTS, REWARD_STEP_CENTS, STATUS_LABELS, orderLabel, shortDate, usd } from "@/lib/money";
import { useApp } from "@/components/Providers";

const REASONS = {
  reward: "Reward",
  reward_reversed: "Reward removed",
  refund: "Refund",
  order: "Used on order",
  order_cancelled: "Returned",
  adjustment: "Adjustment"
};

export default function Account() {
  const router = useRouter();
  const { session, profile, authReady, refreshProfile, signOut, isAdmin } = useApp();
  const [orders, setOrders] = useState([]);
  const [ledger, setLedger] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [message, setMessage] = useState("");
  const [confirmCancel, setConfirmCancel] = useState(null);

  useEffect(() => {
    if (authReady && !session) router.replace("/login?next=/account");
  }, [authReady, session, router]);

  const load = useCallback(async () => {
    if (!session) return;
    const [{ data: orderRows }, { data: ledgerRows }] = await Promise.all([
      supabase.from("orders").select("*").eq("user_id", session.user.id).order("created_at", { ascending: false }),
      supabase.from("credit_ledger").select("*").eq("user_id", session.user.id).order("created_at", { ascending: false })
    ]);
    setOrders(orderRows || []);
    setLedger(ledgerRows || []);
    setLoaded(true);
    refreshProfile();
    // refreshProfile changes every render; session is what matters here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("paid")) setMessage("Thanks for your order. It shows as paid once the payment is confirmed on the blockchain, which can take a few minutes.");
    if (params.get("reset")) setMessage("Your password has been changed.");
  }, []);

  async function cancel(order) {
    if (confirmCancel !== order.id) {
      setConfirmCancel(order.id);
      return;
    }
    setConfirmCancel(null);
    const { error } = await supabase.rpc("cancel_my_order", { p_order: order.id });
    setMessage(error ? "That order couldn't be cancelled. Please contact us." : "Order cancelled. Any credit you used is back in your account.");
    load();
  }

  if (!session || !profile) {
    return <main className="wrap page"><p className="page-lead">Loading…</p></main>;
  }

  const credit = Math.max(0, ledger.reduce((sum, row) => sum + row.amount_cents, 0));
  const spent = Number(profile.spent_cents) || 0;
  const progress = spent % REWARD_STEP_CENTS;
  const toNext = REWARD_STEP_CENTS - progress;
  const rewardsEarned = ledger.reduce((sum, row) => (row.reason === "reward" || row.reason === "reward_reversed" ? sum + row.amount_cents : sum), 0);

  return (
    <main className="wrap page">
      <h1>Hi, {profile.username}</h1>
      <p className="page-lead">Member since {shortDate(profile.created_at)}</p>
      {message && <p className="notice" style={{ marginTop: 12 }}>{message}</p>}

      <div className="stats">
        <div className="stat"><span>Credit balance</span><strong>{usd(credit)}</strong></div>
        <div className="stat"><span>Total spent</span><strong>{usd(spent)}</strong></div>
        <div className="stat">
          <span>Next {usd(REWARD_CENTS)} reward</span>
          <strong>{usd(toNext)} to go</strong>
          <div className="meter" role="progressbar" aria-valuemin={0} aria-valuemax={REWARD_STEP_CENTS} aria-valuenow={progress} aria-label="Progress to next reward">
            <i style={{ width: (progress / REWARD_STEP_CENTS) * 100 + "%" }} />
          </div>
        </div>
      </div>

      <div className="card">
        <h2>Your orders</h2>
        {!loaded ? (
          <p className="hint">Loading…</p>
        ) : orders.length ? (
          <div className="list">
            {orders.map((order) => (
              <div className="row" key={order.id}>
                <div className="row-main">
                  <strong>{order.product_name}{order.quantity > 1 ? " × " + order.quantity : ""}</strong>
                  <span>{shortDate(order.created_at)} · {usd(order.total_cents)}{order.credit_used_cents > 0 ? " + " + usd(order.credit_used_cents) + " credit" : ""}{order.discount_code ? " · code " + order.discount_code : ""}</span>
                  <span>Order {orderLabel(order)}</span>
                  <span><span className={"badge " + order.status}>{STATUS_LABELS[order.status]}</span></span>
                </div>
                {order.status !== "awaiting_payment" && order.status !== "cancelled" && (
                  <div className="row-actions"><Link className="small-button" href={"/order/" + order.id}>View order</Link></div>
                )}
                {order.status === "awaiting_payment" && (
                  <div className="row-actions">
                    <Link className="small-button ghost" href={"/order/" + order.id}>View</Link>
                    {order.invoice_url && <a className="small-button" href={order.invoice_url}>Pay now</a>}
                    <button className={confirmCancel === order.id ? "small-button danger" : "small-button ghost"} type="button" onClick={() => cancel(order)}>
                      {confirmCancel === order.id ? "Confirm cancel" : "Cancel"}
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>
        ) : (
          <p className="hint">No orders yet. <Link href="/#store">Visit the store</Link></p>
        )}
      </div>

      <div className="card">
        <h2>Credit history</h2>
        <p className="hint">You&apos;ve earned {usd(Math.max(0, rewardsEarned))} in rewards so far. Every $20 you spend adds $2.</p>
        {ledger.length ? (
          <div className="list">
            {ledger.map((row) => (
              <div className="row" key={row.id}>
                <div className="row-main">
                  <strong>{REASONS[row.reason] || "Credit"}</strong>
                  <span>{shortDate(row.created_at)}{row.note ? " · " + row.note : ""}</span>
                </div>
                <span className={row.amount_cents > 0 ? "amount plus" : "amount"}>
                  {row.amount_cents > 0 ? "+" : "−"}{usd(Math.abs(row.amount_cents))}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <p className="hint">No credit yet.</p>
        )}
      </div>

      <div className="card stack">
        <h2>Account</h2>
        <p className="hint" style={{ margin: 0 }}>Signed in as {session.user.email}</p>
        <div className="row-actions">
          <Link className="small-button ghost" href="/forgot-password">Change password</Link>
          {isAdmin && <Link className="small-button" href="/admin">Admin</Link>}
          <button className="small-button ghost" type="button" onClick={async () => { await signOut(); router.push("/"); }}>Log out</button>
        </div>
      </div>
    </main>
  );
}
