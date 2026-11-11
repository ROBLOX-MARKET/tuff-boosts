import { supabaseAdmin } from "@/lib/supabaseAdmin";
import { orderLabel } from "@/lib/email";

// Server only. Posts a "new order" embed to the Discord channel webhook saved in email_settings.

const COINS = { usdtbsc: "USDT (BNB Smart Chain)", btc: "Bitcoin (BTC)", ltc: "Litecoin (LTC)", credit: "Account credit" };

function usd(cents) {
  return ((Number(cents) || 0) / 100).toLocaleString("en-US", { style: "currency", currency: "USD" });
}

function clip(text, max = 1000) {
  const value = String(text ?? "");
  return value.length > max ? value.slice(0, max - 1) + "…" : value || "—";
}

export async function notifyNewOrder(orderId) {
  const db = supabaseAdmin();
  const { data: settings } = await db.from("email_settings").select("discord_webhook_url, discord_role_id").eq("id", 1).maybeSingle();
  const hook = String(settings?.discord_webhook_url || "");
  if (!/^https:\/\/(discord\.com|discordapp\.com)\/api\/webhooks\//.test(hook)) return { sent: false };
  const roleId = /^\d{5,25}$/.test(String(settings?.discord_role_id || "")) ? settings.discord_role_id : null;

  // Only once per order.
  const { data: order } = await db
    .from("orders")
    .update({ discord_notified_at: new Date().toISOString() })
    .eq("id", orderId)
    .in("status", ["paid", "completed"])
    .is("discord_notified_at", null)
    .select()
    .maybeSingle();
  if (!order) return { sent: false };

  try {
    const [{ data: profile }, { count: codeCount }] = await Promise.all([
      db.from("profiles").select("username").eq("id", order.user_id).maybeSingle(),
      db.from("product_codes").select("id", { count: "exact", head: true }).eq("order_id", order.id).eq("status", "delivered")
    ]);
    const site = process.env.NEXT_PUBLIC_SITE_URL || "";
    const delivered = (codeCount || 0) > 0
      ? codeCount + " of " + order.quantity + " link" + (order.quantity === 1 ? "" : "s") + " delivered automatically"
      : "Order received, needs completing" + (order.server_link ? "\nServer: " + order.server_link : "");
    const paidAt = Math.floor(new Date(order.paid_at || order.created_at).getTime() / 1000);

    const fields = [
      { name: "💳 Payment Method", value: COINS[order.pay_currency] || String(order.pay_currency).toUpperCase() },
      { name: "💵 Total Amount", value: usd(order.total_cents) + (order.credit_used_cents ? " + " + usd(order.credit_used_cents) + " credit" : "") },
      { name: "🆔 Order ID", value: orderLabel(order) },
      { name: "👤 Customer", value: clip(profile?.username || "Customer", 100) },
      { name: "🛍️ Products", value: clip(order.product_name + " x" + (order.quantity || 1)) }
    ];
    if (order.discount_code) fields.push({ name: "🏷️ Discount", value: order.discount_code + " (−" + usd(order.discount_cents) + ")" });
    fields.push(
      { name: "🧾 Delivered Items", value: clip(delivered) },
      { name: "🕐 Created On", value: "<t:" + paidAt + ":f>" }
    );
    if (site) fields.push({ name: "🔗 View Order", value: site + "/admin" });

    const response = await fetch(hook, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: "Tuff Boosts Orders",
        content: roleId ? "<@&" + roleId + ">" : undefined,
        allowed_mentions: { parse: [], roles: roleId ? [roleId] : [] },
        embeds: [{
          title: "✅ Order Delivered",
          description: "A new order has been successfully processed.",
          color: 0x5bc8f5,
          fields,
          footer: { text: "Tuff Boosts" },
          timestamp: new Date(paidAt * 1000).toISOString()
        }]
      })
    });
    if (!response.ok) throw new Error("Discord said " + response.status);
    return { sent: true };
  } catch (error) {
    await db.from("orders").update({ discord_notified_at: null }).eq("id", orderId);
    console.error("Discord order alert failed", orderId, error?.message);
    return { sent: false };
  }
}
