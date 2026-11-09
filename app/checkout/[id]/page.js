"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";
import { CURRENCIES, usd } from "@/lib/money";
import { useApp } from "@/components/Providers";

export default function Checkout({ params }) {
  const productId = params.id;
  const router = useRouter();
  const { session, authReady } = useApp();
  const [product, setProduct] = useState(null);
  const [missing, setMissing] = useState(false);
  const [stock, setStock] = useState(null);
  const [qty, setQty] = useState(1);
  const [serverLink, setServerLink] = useState("");
  const [codeInput, setCodeInput] = useState("");
  const [code, setCode] = useState("");
  const [codeError, setCodeError] = useState("");
  const [useCredit, setUseCredit] = useState(true);
  const [currency, setCurrency] = useState(CURRENCIES[0].id);
  const [quote, setQuote] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    Promise.all([
      supabase.from("products").select("*").eq("id", productId).eq("active", true).maybeSingle(),
      supabase.rpc("stock_counts")
    ]).then(([{ data }, { data: counts }]) => {
      if (!data) {
        setMissing(true);
        return;
      }
      setProduct(data);
      if (data.stock_tracked) {
        const row = (counts || []).find((item) => item.product_id === data.id);
        setStock(row ? Number(row.available) : 0);
      }
    });
  }, [productId]);

  useEffect(() => {
    if (authReady && !session) router.replace("/login?next=/checkout/" + productId);
  }, [authReady, session, router, productId]);

  const loadQuote = useCallback(async (nextCode, nextUseCredit, nextQty) => {
    const { data, error: quoteError } = await supabase.rpc("quote_order_qty", {
      p_product: productId,
      p_code: nextCode,
      p_use_credit: nextUseCredit,
      p_qty: nextQty
    });
    if (quoteError) {
      setError("Prices couldn't load. Refresh the page to try again.");
      return null;
    }
    return data;
  }, [productId]);

  useEffect(() => {
    if (!session) return;
    loadQuote(code, useCredit, qty).then((data) => {
      if (!data) return;
      if (data.error && code && data.stock === undefined) {
        // The applied code stopped working: drop it and price without it.
        setCode("");
        setCodeError(data.error);
        return;
      }
      if (data.error) {
        setError(data.error);
        if (typeof data.stock === "number") setStock(data.stock);
        setQuote(null);
      } else {
        setError("");
        setQuote(data);
        if (typeof data.stock === "number") setStock(data.stock);
      }
    });
  }, [session, code, useCredit, qty, loadQuote]);

  async function applyCode(event) {
    event.preventDefault();
    setCodeError("");
    const entered = codeInput.trim();
    if (!entered) return;
    const data = await loadQuote(entered, useCredit, qty);
    if (!data) return;
    if (data.error) {
      setCodeError(data.error);
      return;
    }
    setCode(data.code);
    setQuote(data);
  }

  function removeCode() {
    setCode("");
    setCodeInput("");
    setCodeError("");
  }

  const maxQty = Math.max(1, Math.min(100, stock ?? 1));
  function changeQty(next) {
    const value = Math.max(1, Math.min(maxQty, Math.floor(Number(next) || 1)));
    setQty(value);
  }

  async function pay(event) {
    event.preventDefault();
    setError("");
    if (!product.stock_tracked && !serverLink.trim()) {
      setError("Enter your server link.");
      return;
    }
    setBusy(true);
    const { data: current } = await supabase.auth.getSession();
    const response = await fetch("/api/checkout", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: "Bearer " + (current.session?.access_token || "") },
      body: JSON.stringify({ productId, serverLink, code, useCredit, currency, quantity: qty })
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      setBusy(false);
      setError(data.error || "Something went wrong. Please try again.");
      return;
    }
    if (data.url) {
      window.location.href = data.url;
      return;
    }
    router.push("/order/" + data.orderId);
  }

  if (missing) {
    return (
      <main className="wrap narrow page">
        <h1>Product not found</h1>
        <p className="page-lead">This product isn&apos;t available any more.</p>
        <p><Link className="pill" href="/#store">Back to the store</Link></p>
      </main>
    );
  }

  if (!product || !session) {
    return <main className="wrap narrow page"><p className="page-lead">Loading…</p></main>;
  }

  const tracked = product.stock_tracked;
  const outOfStock = tracked && stock === 0;
  const total = quote ? quote.total : product.price_cents * qty;

  return (
    <main className="wrap narrow page">
      <h1>Checkout</h1>
      <p className="page-lead">{product.name}</p>

      <form className="card stack" onSubmit={pay}>
        {tracked ? (
          <div className="stack">
            <label htmlFor="qty">How many?</label>
            <div className="qty">
              <button type="button" aria-label="One less" onClick={() => changeQty(qty - 1)} disabled={qty <= 1}>−</button>
              <input id="qty" type="number" inputMode="numeric" min={1} max={maxQty} value={qty} onChange={(e) => changeQty(e.target.value)} />
              <button type="button" aria-label="One more" onClick={() => changeQty(qty + 1)} disabled={qty >= maxQty}>+</button>
            </div>
            <span className="hint">{outOfStock ? "Out of stock right now." : stock + " in stock. Your links appear on your order page as soon as payment is confirmed."}</span>
          </div>
        ) : (
          <label>Server link
            <input value={serverLink} onChange={(e) => setServerLink(e.target.value)} placeholder="https://discord.gg/yourserver" maxLength={200} required />
            <span className="hint">Double-check this. We can&apos;t move boosts to another server.</span>
          </label>
        )}

        <div className="stack">
          <label htmlFor="code">Discount code</label>
          {code ? (
            <div className="row">
              <div className="row-main"><strong>{code}</strong><span>Saving {usd(quote?.discount)}</span></div>
              <button className="small-button ghost" type="button" onClick={removeCode}>Remove</button>
            </div>
          ) : (
            <div className="inline">
              <input id="code" value={codeInput} onChange={(e) => setCodeInput(e.target.value.toUpperCase())} autoCapitalize="characters" maxLength={30} onKeyDown={(e) => { if (e.key === "Enter") applyCode(e); }} />
              <button className="small-button" type="button" onClick={applyCode}>Apply</button>
            </div>
          )}
          {codeError && <p className="error" role="alert">{codeError}</p>}
        </div>

        {quote && quote.balance > 0 && (
          <label className="check" htmlFor="use-credit">
            <span>Use my credit ({usd(quote.balance)} available)</span>
            <input id="use-credit" type="checkbox" checked={useCredit} onChange={(e) => setUseCredit(e.target.checked)} />
          </label>
        )}

        {total > 0 && (
          <label>Pay with
            <select value={currency} onChange={(e) => setCurrency(e.target.value)}>
              {CURRENCIES.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
            </select>
          </label>
        )}

        <dl className="summary">
          <div><dt>{tracked ? qty + " × " + usd(product.price_cents) : "Price"}</dt><dd>{usd(product.price_cents * qty)}</dd></div>
          {quote?.discount > 0 && <div className="minus"><dt>Discount ({quote.code})</dt><dd>−{usd(quote.discount)}</dd></div>}
          {quote?.credit > 0 && <div className="minus"><dt>Credit</dt><dd>−{usd(quote.credit)}</dd></div>}
          <div className="total"><dt>Total</dt><dd>{usd(total)}</dd></div>
        </dl>

        {error && <p className="error" role="alert">{error}</p>}
        <button className="pill" type="submit" disabled={busy || !quote || outOfStock}>
          {busy ? "Placing order…" : outOfStock ? "Out of stock" : total > 0 ? "Pay " + usd(total) : "Place order with credit"}
        </button>
        <p className="hint">
          {total > 0
            ? "You'll go to a secure payment page. Send the exact amount shown there. This order earns rewards toward your next $2 credit."
            : "Your credit covers the whole order, so there's nothing to pay."}
        </p>
      </form>
    </main>
  );
}
