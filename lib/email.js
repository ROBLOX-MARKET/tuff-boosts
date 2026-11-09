import nodemailer from "nodemailer";
import { supabaseAdmin } from "@/lib/supabaseAdmin";

// Server only. Sends order receipts through any SMTP account (Gmail now, your own domain later).

export function orderLabel(order) {
  return order.order_number ? "#TB-" + order.order_number : "#" + String(order.id).slice(0, 8).toUpperCase();
}

function usd(cents) {
  return ((Number(cents) || 0) / 100).toLocaleString("en-US", { style: "currency", currency: "USD" });
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

const COINS = { usdtbsc: "USDT (BNB Smart Chain)", btc: "Bitcoin", ltc: "Litecoin", credit: "Account credit" };

// Email settings come from Vercel env vars if set, otherwise from the private email_settings table.
async function emailConfig() {
  if (process.env.SMTP_HOST && process.env.SMTP_USER && process.env.SMTP_PASS) {
    return {
      host: process.env.SMTP_HOST,
      port: Number(process.env.SMTP_PORT || 465),
      user: process.env.SMTP_USER,
      pass: process.env.SMTP_PASS,
      from: process.env.EMAIL_FROM || process.env.SMTP_USER
    };
  }
  const { data } = await supabaseAdmin().from("email_settings").select("*").eq("id", 1).maybeSingle();
  if (!data?.smtp_user || !data?.smtp_pass) return null;
  return { host: data.smtp_host, port: Number(data.smtp_port || 465), user: data.smtp_user, pass: data.smtp_pass, from: data.email_from || data.smtp_user };
}

function transport(config) {
  return nodemailer.createTransport({
    host: config.host,
    port: config.port,
    secure: config.port === 465,
    auth: { user: config.user, pass: config.pass }
  });
}

function row(label, value, strong) {
  return `<tr><td style="padding:8px 0;color:#AEBBCC;font-size:15px">${label}</td><td style="padding:8px 0;text-align:right;font-size:15px;color:#F3F8FF;${strong ? "font-weight:800;font-size:18px" : "font-weight:600"}">${value}</td></tr>`;
}

const KINDS = {
  receipt: {
    column: "receipt_sent_at",
    statuses: ["paid", "completed"],
    subject: (o) => "Your Tuff Boosts receipt " + orderLabel(o),
    title: (name) => "Thanks for your order, " + name + "!",
    intro: "Your payment is confirmed. We're getting your boosts sent to your server now."
  },
  completed: {
    column: "completed_email_sent_at",
    statuses: ["completed"],
    subject: (o) => "Your Tuff Boosts order " + orderLabel(o) + " is complete",
    title: () => "Your boosts are live!",
    intro: "Your order is complete and your boosts have been added to your server. Thanks for choosing Tuff Boosts."
  }
};

function receiptHtml({ order, username, discordUrl, siteUrl, reward, business, kind }) {
  const k = KINDS[kind];
  const date = new Date(order.paid_at || order.created_at).toLocaleDateString("en-US", { day: "numeric", month: "long", year: "numeric" });
  const lines = [
    row("Product", esc(order.product_name)),
    row("Price", usd(order.subtotal_cents)),
    order.discount_cents > 0 ? row("Discount (" + esc(order.discount_code) + ")", "−" + usd(order.discount_cents)) : "",
    order.credit_used_cents > 0 ? row("Credit used", "−" + usd(order.credit_used_cents)) : "",
    row("Total paid", usd(order.total_cents), true),
    row("Paid with", esc(COINS[order.pay_currency] || order.pay_currency.toUpperCase()))
  ].join("");

  return `<!doctype html><html><body style="margin:0;background:#07090F;font-family:Arial,Helvetica,sans-serif;color:#F3F8FF">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#07090F"><tr><td align="center" style="padding:32px 16px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px">
<tr><td style="padding-bottom:20px;font-size:24px;font-weight:800;color:#F3F8FF"><span style="color:#5BC8F5">&#11042;</span> Tuff Boosts</td></tr>
<tr><td style="background:#0F141F;border:1px solid #2A3448;border-radius:18px;padding:28px">
<p style="margin:0;color:#5BC8F5;font-weight:700;font-size:14px;letter-spacing:1px">ORDER ${esc(orderLabel(order))}</p>
<h1 style="margin:8px 0 6px;font-size:26px;color:#F3F8FF">${esc(k.title(username))}</h1>
<p style="margin:0 0 20px;color:#AEBBCC;font-size:15px">${k.intro}</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-top:1px solid #2A3448;border-bottom:1px solid #2A3448">${lines}</table>
<p style="margin:18px 0 4px;color:#AEBBCC;font-size:14px">Server</p>
<p style="margin:0;font-size:15px;color:#F3F8FF;word-break:break-all">${esc(order.server_link)}</p>
<p style="margin:14px 0 0;color:#AEBBCC;font-size:14px">Date: ${date}</p>
${reward > 0 && kind === "receipt" ? `<p style="margin:18px 0 0;padding:12px 14px;background:#0B2236;border:1px solid #5BC8F5;border-radius:12px;color:#BDEBFF;font-size:15px">You earned <strong>${usd(reward)} credit</strong> with this order. It's in your account for next time.</p>` : ""}
<table role="presentation" cellpadding="0" cellspacing="0" style="margin-top:24px"><tr>
<td style="padding-right:10px"><a href="${esc(siteUrl)}/account" style="display:inline-block;padding:12px 20px;background:#5BC8F5;color:#04101F;border-radius:999px;font-weight:700;text-decoration:none">View my order</a></td>
<td><a href="${esc(discordUrl)}" style="display:inline-block;padding:12px 20px;background:#5865F2;color:#FFFFFF;border-radius:999px;font-weight:700;text-decoration:none">Join our Discord</a></td>
</tr></table>
<p style="margin:22px 0 0;color:#AEBBCC;font-size:14px">Need help? Reply in live chat on our site or message us on Discord, and include your order number ${esc(orderLabel(order))}.</p>
</td></tr>
<tr><td style="padding:18px 4px 0;color:#7C8799;font-size:12px">${esc(business || "Tuff Boosts")} · Prices in US dollars. Tuff Boosts isn't connected to or endorsed by Discord.</td></tr>
</table></td></tr></table></body></html>`;
}

function receiptText({ order, username, discordUrl, siteUrl, kind }) {
  const k = KINDS[kind];
  return [
    "Tuff Boosts: order " + orderLabel(order),
    "",
    k.title(username) + " " + k.intro,
    "",
    "Product: " + order.product_name,
    "Price: " + usd(order.subtotal_cents),
    order.discount_cents > 0 ? "Discount (" + order.discount_code + "): -" + usd(order.discount_cents) : null,
    order.credit_used_cents > 0 ? "Credit used: -" + usd(order.credit_used_cents) : null,
    "Total paid: " + usd(order.total_cents),
    "Server: " + order.server_link,
    "",
    "Your orders: " + siteUrl + "/account",
    "Discord: " + discordUrl
  ].filter((line) => line !== null).join("\n");
}

// Sends each email once per order. kind: "receipt" (paid) or "completed". force resends.
export async function sendReceipt(orderId, { force = false, kind = "receipt" } = {}) {
  const k = KINDS[kind];
  if (!k) return { sent: false, reason: "Unknown email type." };
  const config = await emailConfig();
  if (!config) return { sent: false, reason: "Email isn't set up yet." };
  const db = supabaseAdmin();

  let query = db.from("orders").update({ [k.column]: new Date().toISOString() }).eq("id", orderId).in("status", k.statuses);
  if (!force) query = query.is(k.column, null);
  const { data: claimed } = await query.select().maybeSingle();
  if (!claimed) return { sent: false, reason: force ? "This order isn't at the right status for that email." : "Already sent." };

  try {
    const [{ data: userData }, { data: profile }, { data: settings }, { data: rewards }] = await Promise.all([
      db.auth.admin.getUserById(claimed.user_id),
      db.from("profiles").select("username").eq("id", claimed.user_id).maybeSingle(),
      db.from("settings").select("discord_url, business_name").eq("id", 1).maybeSingle(),
      db.from("credit_ledger").select("amount_cents").eq("order_id", claimed.id).eq("reason", "reward")
    ]);
    const to = userData?.user?.email;
    if (!to) throw new Error("No email for this customer.");
    const info = {
      order: claimed,
      username: profile?.username || "there",
      discordUrl: String(settings?.discord_url || "").startsWith("https://") ? settings.discord_url : "https://discord.gg/TuffBoosts",
      siteUrl: process.env.NEXT_PUBLIC_SITE_URL || "",
      reward: (rewards || []).reduce((sum, r) => sum + r.amount_cents, 0),
      business: settings?.business_name,
      kind
    };
    await transport(config).sendMail({
      from: config.from,
      to,
      subject: k.subject(claimed),
      text: receiptText(info),
      html: receiptHtml(info)
    });
    return { sent: true };
  } catch (error) {
    // Let it be tried again later.
    if (!force) await db.from("orders").update({ [k.column]: null }).eq("id", orderId);
    console.error("Receipt email failed", orderId, error?.message);
    return { sent: false, reason: "The email didn't send. Check the Gmail App Password is right." };
  }
}
