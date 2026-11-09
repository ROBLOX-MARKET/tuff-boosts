"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";
import { CATEGORIES, STATUS_LABELS, orderLabel, shortDate, toCents, usd } from "@/lib/money";
import { useApp } from "@/components/Providers";

const TABS = [
  { id: "orders", label: "Orders" },
  { id: "customers", label: "Customers" },
  { id: "products", label: "Products" },
  { id: "codes", label: "Discount codes" },
  { id: "site", label: "Site" }
];

export default function Admin() {
  const router = useRouter();
  const { session, profile, isAdmin, authReady } = useApp();
  const [tab, setTab] = useState("orders");

  useEffect(() => {
    if (authReady && !session) router.replace("/login?next=/admin");
  }, [authReady, session, router]);

  if (!authReady || (session && !profile)) {
    return <main className="wrap page"><p className="page-lead">Loading…</p></main>;
  }
  if (!isAdmin) {
    return <main className="wrap page"><h1>Admins only</h1><p className="page-lead">This page is for the shop owner.</p></main>;
  }

  return (
    <main className="wrap page">
      <h1>Admin</h1>
      <div className="admin-tabs" role="tablist">
        {TABS.map((item) => (
          <button key={item.id} type="button" role="tab" className="tab" aria-selected={tab === item.id} onClick={() => setTab(item.id)}>{item.label}</button>
        ))}
      </div>
      {tab === "orders" && <Orders />}
      {tab === "customers" && <Customers />}
      {tab === "products" && <Products />}
      {tab === "codes" && <Codes />}
      {tab === "site" && <Site />}
    </main>
  );
}

/* ---------------- Orders ---------------- */

function Orders() {
  const [orders, setOrders] = useState([]);
  const [names, setNames] = useState({});
  const [filter, setFilter] = useState("paid");
  const [confirm, setConfirm] = useState(null);
  const [status, setStatus] = useState("");

  const load = useCallback(async () => {
    let query = supabase.from("orders").select("*").order("created_at", { ascending: false }).limit(200);
    if (filter !== "all") query = query.eq("status", filter);
    const { data } = await query;
    setOrders(data || []);
    const ids = [...new Set((data || []).map((o) => o.user_id))];
    if (ids.length) {
      const { data: people } = await supabase.from("profiles").select("id, username").in("id", ids);
      setNames(Object.fromEntries((people || []).map((p) => [p.id, p.username])));
    }
  }, [filter]);

  useEffect(() => { load(); }, [load]);

  async function act(order, action) {
    const key = order.id + action;
    if (action !== "completed" && confirm !== key) {
      setConfirm(key);
      return;
    }
    setConfirm(null);
    if (action === "receipt") {
      setStatus("Sending receipt…");
      setStatus(await requestReceipt(order.id, true, "receipt"));
      load();
      return;
    }
    const { error } = action === "refund"
      ? await supabase.rpc("admin_refund_to_credit", { p_order: order.id })
      : await supabase.rpc("admin_set_order_status", { p_order: order.id, p_status: action });
    if (error) {
      setStatus("That didn't work: " + error.message);
    } else if (action === "paid") {
      setStatus("Order marked paid. " + (await requestReceipt(order.id, false, "receipt")));
    } else if (action === "completed") {
      setStatus("Order completed. " + (await requestReceipt(order.id, false, "completed")));
    } else {
      setStatus("Order updated.");
    }
    load();
  }

  async function requestReceipt(orderId, resend, kind) {
    const { data } = await supabase.auth.getSession();
    const response = await fetch("/api/receipt", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: "Bearer " + (data.session?.access_token || "") },
      body: JSON.stringify({ orderId, resend, kind })
    });
    const result = await response.json().catch(() => ({}));
    const what = kind === "completed" ? "Completed email" : "Receipt";
    return result.sent ? what + " sent to the customer." : what + " not sent: " + (result.reason || result.error || "unknown problem");
  }

  const button = (order, action, label, danger) => {
    const confirming = confirm === order.id + action;
    return (
      <button type="button" className={confirming || danger ? "small-button danger" : "small-button"} onClick={() => act(order, action)}>
        {confirming ? "Confirm" : label}
      </button>
    );
  };

  return (
    <div className="card">
      <div className="inline" style={{ alignItems: "center", justifyContent: "space-between", flexWrap: "wrap" }}>
        <h2>Orders</h2>
        <select value={filter} onChange={(e) => setFilter(e.target.value)} style={{ maxWidth: 220 }} aria-label="Show orders">
          <option value="paid">To do (paid)</option>
          <option value="awaiting_payment">Awaiting payment</option>
          <option value="completed">Completed</option>
          <option value="refunded">Refunded</option>
          <option value="cancelled">Cancelled</option>
          <option value="all">All orders</option>
        </select>
      </div>
      {status && <p className="status-line">{status}</p>}
      {orders.length ? (
        <div className="list">
          {orders.map((order) => (
            <div className="row" key={order.id}>
              <div className="row-main">
                <strong>{order.product_name} · {names[order.user_id] || "Customer"}</strong>
                <span>{shortDate(order.created_at)} · Order {orderLabel(order)}{order.receipt_sent_at ? " · Receipt sent" : ""} · <span className={"badge " + order.status}>{STATUS_LABELS[order.status]}</span></span>
                <span>Paid {usd(order.total_cents)} in {order.pay_currency.toUpperCase()}{order.credit_used_cents ? " + " + usd(order.credit_used_cents) + " credit" : ""}{order.discount_code ? " · code " + order.discount_code + " (−" + usd(order.discount_cents) + ")" : ""}</span>
                <span>Server: {order.server_link}</span>
                {order.payment_note && <span className="error">{order.payment_note}</span>}
              </div>
              <div className="row-actions">
                {order.status === "paid" && button(order, "completed", "Mark completed")}
                {(order.status === "paid" || order.status === "completed") && button(order, "refund", "Refund to credit")}
                {(order.status === "paid" || order.status === "completed") && button(order, "receipt", order.receipt_sent_at ? "Resend receipt" : "Send receipt")}
                {(order.status === "awaiting_payment" || order.status === "cancelled") && button(order, "paid", "Mark paid")}
                {order.status === "awaiting_payment" && button(order, "cancelled", "Cancel")}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <p className="hint">No orders here.</p>
      )}
    </div>
  );
}

/* ---------------- Customers ---------------- */

function Customers() {
  const [people, setPeople] = useState([]);
  const [error, setError] = useState("");
  const [adjusting, setAdjusting] = useState(null);
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  const [status, setStatus] = useState("");

  const load = useCallback(async () => {
    const { data, error: loadError } = await supabase.rpc("admin_customers");
    if (loadError) setError("Customers couldn't load.");
    else setPeople(data || []);
  }, []);

  useEffect(() => { load(); }, [load]);

  async function adjust(event) {
    event.preventDefault();
    const negative = amount.trim().startsWith("-");
    const cents = toCents(amount.trim().replace(/^-/, ""));
    if (!cents) {
      setStatus("Enter an amount like 5 or -5.");
      return;
    }
    const { error: adjustError } = await supabase.rpc("admin_adjust_credit", {
      p_user: adjusting.id,
      p_cents: negative ? -cents : cents,
      p_note: note.trim()
    });
    setStatus(adjustError ? "That didn't save." : "Credit updated for " + adjusting.username + ".");
    setAdjusting(null);
    setAmount("");
    setNote("");
    load();
  }

  return (
    <div className="card">
      <h2>Customers ({people.length})</h2>
      <p className="hint">Everyone who has signed up. Only usernames are shown; emails and passwords stay private.</p>
      {error && <p className="error">{error}</p>}
      {status && <p className="status-line">{status}</p>}
      {adjusting && (
        <form className="card stack" onSubmit={adjust}>
          <h3>Adjust credit for {adjusting.username}</h3>
          <div className="grid-2">
            <label>Amount in $<input value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="5 or -5" required /></label>
            <label>Note (customer sees this)<input value={note} onChange={(e) => setNote(e.target.value)} maxLength={200} /></label>
          </div>
          <div className="row-actions">
            <button className="small-button" type="submit">Save</button>
            <button className="small-button ghost" type="button" onClick={() => setAdjusting(null)}>Cancel</button>
          </div>
        </form>
      )}
      <div className="table-scroll">
        <table className="data">
          <thead>
            <tr><th>Username</th><th>Joined</th><th className="num">Orders</th><th className="num">Spent</th><th className="num">Credit</th><th></th></tr>
          </thead>
          <tbody>
            {people.map((person) => (
              <tr key={person.id}>
                <td>{person.username}</td>
                <td>{shortDate(person.created_at)}</td>
                <td className="num">{person.order_count}</td>
                <td className="num">{usd(person.spent_cents)}</td>
                <td className="num">{usd(Math.max(0, person.credit_cents))}</td>
                <td><button className="small-button ghost" type="button" onClick={() => setAdjusting(person)}>Credit</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!people.length && !error && <p className="hint">No sign-ups yet.</p>}
    </div>
  );
}

/* ---------------- Products ---------------- */

const EMPTY_PRODUCT = { name: "", category: "boosts", price: "", sold: "0", description: "", image_url: "" };
const IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp", "image/gif"];

async function uploadProductImage(file) {
  if (!IMAGE_TYPES.includes(file.type)) throw new Error("Use a PNG, JPG, WEBP or GIF image.");
  if (file.size > 5 * 1024 * 1024) throw new Error("Images must be under 5 MB.");
  const ext = file.type.split("/")[1].replace("jpeg", "jpg");
  const path = crypto.randomUUID() + "." + ext;
  const { error } = await supabase.storage.from("product-images").upload(path, file, { contentType: file.type });
  if (error) throw new Error("The image didn't upload. Check you ran the image setup in Supabase.");
  return supabase.storage.from("product-images").getPublicUrl(path).data.publicUrl;
}

function Products() {
  const [products, setProducts] = useState([]);
  const [form, setForm] = useState(EMPTY_PRODUCT);
  const [editing, setEditing] = useState(null);
  const [confirm, setConfirm] = useState(null);
  const [status, setStatus] = useState("");
  const [imageFile, setImageFile] = useState(null);
  const [fileKey, setFileKey] = useState(0);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const { data } = await supabase.from("products").select("*").order("created_at", { ascending: true });
    setProducts(data || []);
  }, []);

  useEffect(() => { load(); }, [load]);

  const field = (key) => ({ value: form[key], onChange: (e) => setForm({ ...form, [key]: e.target.value }) });

  async function save(event) {
    event.preventDefault();
    const price = toCents(form.price);
    if (!form.name.trim() || !price) {
      setStatus("Add a name and a price like 4.99.");
      return;
    }
    setBusy(true);
    let imageUrl = form.image_url || "";
    if (imageFile) {
      try {
        imageUrl = await uploadProductImage(imageFile);
      } catch (uploadError) {
        setBusy(false);
        setStatus(uploadError.message);
        return;
      }
    }
    const row = {
      name: form.name.trim(),
      category: form.category,
      price_cents: price,
      sold: Math.max(0, Math.floor(Number(form.sold) || 0)),
      description: form.description.trim(),
      image_url: imageUrl
    };
    const { error } = editing
      ? await supabase.from("products").update(row).eq("id", editing)
      : await supabase.from("products").insert(row);
    setBusy(false);
    setStatus(error ? "That didn't save." : (editing ? "Saved " : "Added ") + row.name + ".");
    if (!error) {
      setForm(EMPTY_PRODUCT);
      setEditing(null);
      setImageFile(null);
      setFileKey((k) => k + 1);
    }
    load();
  }

  function edit(product) {
    setEditing(product.id);
    setImageFile(null);
    setFileKey((k) => k + 1);
    setForm({
      name: product.name,
      category: product.category,
      price: (product.price_cents / 100).toFixed(2),
      sold: String(product.sold),
      description: product.description,
      image_url: product.image_url || ""
    });
  }

  async function toggle(product) {
    await supabase.from("products").update({ active: !product.active }).eq("id", product.id);
    load();
  }

  async function remove(product) {
    if (confirm !== product.id) {
      setConfirm(product.id);
      return;
    }
    setConfirm(null);
    const { error } = await supabase.from("products").delete().eq("id", product.id);
    setStatus(error ? "That product couldn't be deleted." : "Deleted " + product.name + ".");
    load();
  }

  return (
    <>
      <form className="card stack" onSubmit={save}>
        <h2>{editing ? "Edit product" : "Add product"}</h2>
        <div className="grid-2">
          <label>Product name<input {...field("name")} maxLength={80} required /></label>
          <label>Category
            <select {...field("category")}>
              {CATEGORIES.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
            </select>
          </label>
          <label>Price in $<input {...field("price")} inputMode="decimal" placeholder="4.99" required /></label>
          <label>Number sold<input {...field("sold")} type="number" min="0" step="1" /></label>
        </div>
        <label>Description<textarea {...field("description")} rows={2} maxLength={240} /></label>
        <div className="stack">
          <label>Image (optional)
            <input key={fileKey} type="file" accept="image/png,image/jpeg,image/webp,image/gif" onChange={(e) => setImageFile(e.target.files?.[0] || null)} />
            <span className="hint">PNG, JPG, WEBP or GIF, under 5 MB. Leave empty for no image.</span>
          </label>
          {form.image_url && !imageFile && (
            <div className="row">
              <img className="thumb" src={form.image_url} alt="" />
              <button className="small-button ghost" type="button" onClick={() => setForm({ ...form, image_url: "" })}>Remove image</button>
            </div>
          )}
        </div>
        {status && <p className="status-line">{status}</p>}
        <div className="row-actions">
          <button className="small-button" type="submit" disabled={busy}>{busy ? "Saving…" : editing ? "Save changes" : "Add product"}</button>
          {editing && <button className="small-button ghost" type="button" onClick={() => { setEditing(null); setForm(EMPTY_PRODUCT); setImageFile(null); setFileKey((k) => k + 1); }}>Cancel</button>}
        </div>
      </form>
      <div className="card">
        <h2>Your products</h2>
        {products.length ? (
          <div className="list">
            {products.map((product) => (
              <div className="row" key={product.id}>
                {product.image_url && <img className="thumb" src={product.image_url} alt="" />}
                <div className="row-main" style={{ flex: 1 }}>
                  <strong>{product.name} · {usd(product.price_cents)}</strong>
                  <span>{CATEGORIES.find((c) => c.id === product.category)?.label} · {product.sold} sold{product.active ? "" : " · Hidden"}</span>
                </div>
                <div className="row-actions">
                  <button className="small-button ghost" type="button" onClick={() => edit(product)}>Edit</button>
                  <button className="small-button ghost" type="button" onClick={() => toggle(product)}>{product.active ? "Hide" : "Show"}</button>
                  <button className={confirm === product.id ? "small-button danger" : "small-button ghost"} type="button" onClick={() => remove(product)}>
                    {confirm === product.id ? "Confirm delete" : "Delete"}
                  </button>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="hint">Add your first product above.</p>
        )}
      </div>
    </>
  );
}

/* ---------------- Discount codes ---------------- */

const EMPTY_CODE = { code: "", kind: "percent", amount: "", min: "", maxUses: "", expires: "" };

function Codes() {
  const [codes, setCodes] = useState([]);
  const [form, setForm] = useState(EMPTY_CODE);
  const [confirm, setConfirm] = useState(null);
  const [status, setStatus] = useState("");

  const load = useCallback(async () => {
    const { data } = await supabase.from("discount_codes").select("*").order("created_at", { ascending: false });
    setCodes(data || []);
  }, []);

  useEffect(() => { load(); }, [load]);

  const field = (key) => ({ value: form[key], onChange: (e) => setForm({ ...form, [key]: e.target.value }) });

  async function save(event) {
    event.preventDefault();
    const code = form.code.trim().toUpperCase();
    if (!/^[A-Z0-9_-]{2,30}$/.test(code)) {
      setStatus("Codes are 2 to 30 letters, numbers, dashes or underscores.");
      return;
    }
    let amount;
    if (form.kind === "percent") {
      amount = Math.floor(Number(form.amount));
      if (!(amount >= 1 && amount <= 100)) {
        setStatus("Enter a percentage from 1 to 100.");
        return;
      }
    } else {
      amount = toCents(form.amount);
      if (!amount) {
        setStatus("Enter a dollar amount like 2.50.");
        return;
      }
    }
    const row = {
      code,
      kind: form.kind,
      amount,
      min_cents: form.min ? toCents(form.min) || 0 : 0,
      max_uses: form.maxUses ? Math.max(1, Math.floor(Number(form.maxUses))) : null,
      expires_at: form.expires ? new Date(form.expires + "T23:59:59").toISOString() : null
    };
    const { error } = await supabase.from("discount_codes").insert(row);
    setStatus(error ? (error.code === "23505" ? "That code already exists." : "That code couldn't be saved.") : "Added " + code + ".");
    if (!error) setForm(EMPTY_CODE);
    load();
  }

  async function toggle(row) {
    await supabase.from("discount_codes").update({ active: !row.active }).eq("code", row.code);
    load();
  }

  async function remove(row) {
    if (confirm !== row.code) {
      setConfirm(row.code);
      return;
    }
    setConfirm(null);
    await supabase.from("discount_codes").delete().eq("code", row.code);
    load();
  }

  const describe = (row) => (row.kind === "percent" ? row.amount + "% off" : usd(row.amount) + " off");

  return (
    <>
      <form className="card stack" onSubmit={save}>
        <h2>Add discount code</h2>
        <div className="grid-2">
          <label>Code<input {...field("code")} onChange={(e) => setForm({ ...form, code: e.target.value.toUpperCase() })} maxLength={30} placeholder="WELCOME10" required /></label>
          <label>Type
            <select {...field("kind")}>
              <option value="percent">Percentage off</option>
              <option value="fixed">Dollar amount off</option>
            </select>
          </label>
          <label>{form.kind === "percent" ? "Percent off" : "Amount off in $"}<input {...field("amount")} inputMode="decimal" required /></label>
          <label>Minimum order in $ (optional)<input {...field("min")} inputMode="decimal" /></label>
          <label>Max uses (optional)<input {...field("maxUses")} type="number" min="1" step="1" /></label>
          <label>Expires (optional)<input {...field("expires")} type="date" /></label>
        </div>
        {status && <p className="status-line">{status}</p>}
        <div><button className="small-button" type="submit">Add code</button></div>
      </form>
      <div className="card">
        <h2>Your codes</h2>
        {codes.length ? (
          <div className="list">
            {codes.map((row) => (
              <div className="row" key={row.code}>
                <div className="row-main">
                  <strong>{row.code} · {describe(row)}</strong>
                  <span>
                    Used {row.uses}{row.max_uses ? " of " + row.max_uses : ""} times
                    {row.min_cents ? " · min " + usd(row.min_cents) : ""}
                    {row.expires_at ? " · expires " + shortDate(row.expires_at) : ""}
                    {row.active ? "" : " · Off"}
                  </span>
                </div>
                <div className="row-actions">
                  <button className="small-button ghost" type="button" onClick={() => toggle(row)}>{row.active ? "Turn off" : "Turn on"}</button>
                  <button className={confirm === row.code ? "small-button danger" : "small-button ghost"} type="button" onClick={() => remove(row)}>
                    {confirm === row.code ? "Confirm delete" : "Delete"}
                  </button>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="hint">No codes yet.</p>
        )}
      </div>
    </>
  );
}

/* ---------------- Site settings ---------------- */

function Site() {
  const { site, refreshSite } = useApp();
  const [form, setForm] = useState(site);
  const [status, setStatus] = useState("");

  useEffect(() => { setForm(site); }, [site]);

  const field = (key) => ({ value: form[key] ?? "", onChange: (e) => setForm({ ...form, [key]: e.target.value }) });

  async function save(event) {
    event.preventDefault();
    if (!String(form.discord_url).startsWith("https://")) {
      setStatus("Enter a Discord link that starts with https://");
      return;
    }
    const { error } = await supabase.from("settings").update({
      discord_url: form.discord_url.trim(),
      boosts_sold: Math.max(0, Math.floor(Number(form.boosts_sold) || 0)),
      business_name: form.business_name.trim(),
      contact_email: form.contact_email.trim(),
      business_address: form.business_address.trim(),
      coming_soon: form.coming_soon
    }).eq("id", 1);
    setStatus(error ? "That didn't save." : "Site details saved.");
    refreshSite();
  }

  return (
    <form className="card stack" onSubmit={save}>
      <h2>Site</h2>
      <div className="grid-2">
        <label>Boosts sold<input {...field("boosts_sold")} type="number" min="0" step="1" /></label>
        <label>Discord link<input {...field("discord_url")} type="url" required /></label>
        <label>Business or trader name<input {...field("business_name")} maxLength={100} /></label>
        <label>Contact email<input {...field("contact_email")} type="email" maxLength={120} /></label>
      </div>
      <label>Business address<input {...field("business_address")} maxLength={200} /></label>
      <h3>Coming soon</h3>
      {CATEGORIES.map((category) => (
        <label className="check" key={category.id} htmlFor={"soon-" + category.id}>
          <span>{category.label}: show Coming soon</span>
          <input
            id={"soon-" + category.id}
            type="checkbox"
            checked={Boolean((form.coming_soon || {})[category.id])}
            onChange={(e) => setForm({ ...form, coming_soon: { ...form.coming_soon, [category.id]: e.target.checked } })}
          />
        </label>
      ))}
      {status && <p className="status-line">{status}</p>}
      <div><button className="small-button" type="submit">Save site details</button></div>
    </form>
  );
}
