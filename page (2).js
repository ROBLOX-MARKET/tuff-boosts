"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { supabase } from "@/lib/supabase";
import { CATEGORIES, usd } from "@/lib/money";
import Logo from "@/components/Logo";
import { discordUrl, useApp } from "@/components/Providers";

function ProductCard({ product }) {
  return (
    <article className="product">
      <h4>{product.name}</h4>
      <div className="price">{usd(product.price_cents)}</div>
      {product.description && <p>{product.description}</p>}
      <Link className="pill" href={"/checkout/" + product.id}>Buy now</Link>
    </article>
  );
}

function Grid({ products }) {
  return (
    <div className="products">
      {products.map((product) => <ProductCard key={product.id} product={product} />)}
    </div>
  );
}

function useCountUp(target) {
  const [shown, setShown] = useState(0);
  const from = useRef(0);
  useEffect(() => {
    const start = from.current;
    from.current = target;
    if (start === target || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setShown(target);
      return;
    }
    const began = performance.now();
    let frame;
    const step = (now) => {
      const progress = Math.min((now - began) / 600, 1);
      setShown(Math.round(start + (target - start) * (1 - Math.pow(1 - progress, 3))));
      if (progress < 1) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [target]);
  return shown;
}

export default function Home() {
  const { site } = useApp();
  const [products, setProducts] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [category, setCategory] = useState("boosts");
  const sold = useCountUp(Math.max(0, Number(site.boosts_sold) || 0));

  useEffect(() => {
    supabase
      .from("products")
      .select("*")
      .eq("active", true)
      .order("price_cents", { ascending: true })
      .then(({ data }) => {
        setProducts(data || []);
        setLoaded(true);
      });
  }, []);

  const comingSoon = (id) => Boolean((site.coming_soon || {})[id]);
  const current = CATEGORIES.find((item) => item.id === category);
  const inCategory = products.filter((product) => product.category === category);
  const best = products
    .filter((product) => !comingSoon(product.category))
    .sort((a, b) => (b.sold || 0) - (a.sold || 0))
    .slice(0, 3);

  return (
    <>
      <div className="hero">
        <svg className="smoke" aria-hidden="true" preserveAspectRatio="none" viewBox="0 0 1280 620">
          <defs>
            <filter id="hero-deep" x="0" y="0" width="100%" height="100%">
              <feTurbulence type="fractalNoise" baseFrequency="0.009 0.0035" numOctaves="6" seed="5" />
              <feColorMatrix type="matrix" values="0 0 0 0 0.05  0 0 0 0 0.28  0 0 0 0 1  3.8 0 0 0 -1.35" />
            </filter>
            <linearGradient id="hero-rise" x1="0" y1="1" x2="0" y2="0">
              <stop offset="0" stopColor="#FFFFFF" stopOpacity="0.7" />
              <stop offset="0.7" stopColor="#FFFFFF" stopOpacity="0.16" />
              <stop offset="1" stopColor="#FFFFFF" stopOpacity="0" />
            </linearGradient>
            <mask id="hero-mask"><rect width="1280" height="620" fill="url(#hero-rise)" /></mask>
          </defs>
          <g mask="url(#hero-mask)"><rect width="1280" height="620" filter="url(#hero-deep)" /></g>
        </svg>
        <div className="wrap">
          <div className="hero-brand"><Logo size={56} />Tuff Boosts</div>
          <h1>Your Discord <span className="accent">Boosts &amp; Nitro</span></h1>
          <p className="lead">Pick a product, pay with USDT, Bitcoin or Litecoin, and earn $2 credit for every $20 you spend.</p>
          <div className="actions">
            <a className="pill" href="#store">Start boosting</a>
            <a className="pill discord" href={discordUrl(site)} target="_blank" rel="noopener noreferrer">Join Discord</a>
          </div>
          <p className="sold" aria-live="polite"><strong>{sold.toLocaleString("en-US")}</strong><span>boosts sold</span></p>
        </div>
      </div>

      <main className="wrap">
        <section id="store" aria-labelledby="store-title">
          <h2 className="title" id="store-title">Our Products</h2>
          <p className="subtitle">Choose a category to see what&apos;s available.</p>
          <div className="tabs" role="tablist" aria-label="Product categories">
            {CATEGORIES.map((item) => (
              <button key={item.id} type="button" className="tab" role="tab" aria-selected={category === item.id} onClick={() => setCategory(item.id)}>
                {item.label}
              </button>
            ))}
          </div>
          <div className="showcase">
            <h3><span>{current.first} </span><span className="accent">{current.second}</span></h3>
            {comingSoon(category) && <span className="soon">Coming soon</span>}
          </div>
          {!comingSoon(category) && (
            inCategory.length ? <Grid products={inCategory} /> : <p className="empty">{loaded ? "No products are listed here yet." : "Loading products…"}</p>
          )}
        </section>

        <section aria-labelledby="best-title">
          <h2 className="title" id="best-title">Best Sellers</h2>
          {best.length ? <Grid products={best} /> : <p className="empty">Best sellers will appear here when products are added.</p>}
        </section>

        <section id="how" aria-labelledby="how-title">
          <h2 className="title" id="how-title">How It Works</h2>
          <ol className="steps">
            <li>Choose a product<span>Pick what you need and add your server link.</span></li>
            <li>Pay in crypto<span>Use a discount code or your credit, then pay in USDT, BTC or LTC.</span></li>
            <li>Earn rewards<span>Get $2 credit for every $20 you spend, shown in your account.</span></li>
          </ol>
        </section>

        <section id="faq" aria-labelledby="faq-title">
          <h2 className="title" id="faq-title">FAQ</h2>
          <div className="faq">
            <details><summary>How do I order?</summary><p>Create an account, choose a product, enter your server link, and pay with USDT (BNB Smart Chain), Bitcoin or Litecoin.</p></details>
            <details><summary>How do rewards work?</summary><p>Every time your total spending passes another $20, we add $2 credit to your account. You can use credit on any order.</p></details>
            <details><summary>How do refunds work?</summary><p>Refunds are added to your account as credit you can spend on your next order. If the law gives you the right to your money back instead, contact us.</p></details>
            <details><summary>What if I give the wrong server?</summary><p>Check your server link before paying. We can&apos;t refund orders sent to the wrong server unless the law requires us to.</p></details>
            <details><summary>What if I send the wrong amount?</summary><p>Send the exact amount shown on the payment page. If you pay too little, contact us in live chat with your order number.</p></details>
            <details><summary>I forgot my password.</summary><p>Use &quot;Forgot password&quot; on the login page and we&apos;ll email you a link to set a new one.</p></details>
          </div>
        </section>
      </main>
    </>
  );
}
