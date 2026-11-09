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
    supabase
      .from("products")
      .select("*")
      .eq("id", productId)
      .eq("active", true)
      .maybeSingle()
      .then(({ data }) => {
        if (data) setProduct(data);
        else setMissing(true);
      });
  }, [productId]);

  useEffect(() => {
    if (authReady && !session) router.replace("/login?next=/checkout/" + productId);
  }, [authReady, session, router, productId]);

  const loadQuote = useCallback(async (nextCode, nextUseCredit) => {
    const { data, error: quoteError } = await supabase.rpc("quote_order", {
      p_product: productId,
      p_code: nextCode,
      p_use_credit: nextUseCredit
    });
    if (quoteError) {
      setError("Prices couldn't load. Refresh the page to try again.");
      return null;
    }
    return data;
  }, [productId]);

  useEffect(() => {
    if (!session) return;
    loadQuote(code, useCredit).then((data) => {
      if (!data) return;
      if (data.error && code) {
        // The applied code stopped working: drop it and price without it.
        setCode("");
        setCodeError(data.error);
        return;
      }
      if (data.error) setError(data.error);
      else setQuote(data);
    });
  }, [session, code, useCredit, loadQuote]);

  async function applyCode(event) {
    event.preventDefault();
    setCodeError("");
    const entered = codeInput.trim();
    if (!entered) return;
    const data = await loadQuote(entered, useCredit);
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

  async function pay(event) {
    event.preventDefault();
    setError("");
    if (!serverLink.trim()) {
      setError("Enter your server link.");
      return;
    }
    setBusy(true);
    const { data: current } = await supabase.auth.getSession();
    const response = await fetch("/api/checkout", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: "Bearer " + (current.session?.access_token || "") },
      body: JSON.stringify({ productId, serverLink, code, useCredit, currency })
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
    router.push("/account?paid=" + data.orderId);
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

  const total = quote ? quote.total : product.price_cents;

  return (
    <main className="wrap narrow page">
      <h1>Checkout</h1>
      <p className="page-lead">{product.name}</p>

      <form className="card stack" onSubmit={pay}>
        <label>Server link
          <input value={serverLink} onChange={(e) => setServerLink(e.target.value)} placeholder="https://discord.gg/yourserver" maxLength={200} required />
          <span className="hint">Double-check this. We can&apos;t move boosts to another server.</span>
        </label>

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
          <div><dt>Price</dt><dd>{usd(product.price_cents)}</dd></div>
          {quote?.discount > 0 && <div className="minus"><dt>Discount ({quote.code})</dt><dd>−{usd(quote.discount)}</dd></div>}
          {quote?.credit > 0 && <div className="minus"><dt>Credit</dt><dd>−{usd(quote.credit)}</dd></div>}
          <div className="total"><dt>Total</dt><dd>{usd(total)}</dd></div>
        </dl>

        {error && <p className="error" role="alert">{error}</p>}
        <button className="pill" type="submit" disabled={busy || !quote}>
          {busy ? "Placing order…" : total > 0 ? "Pay " + usd(total) : "Place order with credit"}
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
