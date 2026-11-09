import crypto from "crypto";

// Everything specific to the crypto gateway lives in this file.
// To switch gateway later, replace createInvoice and verifyWebhook.

const API = "https://api.nowpayments.io/v1";

export async function createInvoice({ orderId, totalCents, currency, description }) {
  const site = process.env.NEXT_PUBLIC_SITE_URL;
  const response = await fetch(API + "/invoice", {
    method: "POST",
    headers: { "x-api-key": process.env.NOWPAYMENTS_API_KEY, "Content-Type": "application/json" },
    body: JSON.stringify({
      price_amount: totalCents / 100,
      price_currency: "usd",
      pay_currency: currency,
      order_id: orderId,
      order_description: description,
      ipn_callback_url: site + "/api/webhooks/nowpayments",
      success_url: site + "/account?paid=" + orderId,
      cancel_url: site + "/account"
    })
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.invoice_url) {
    throw new Error(data.message || "The payment page couldn't be created.");
  }
  return { id: String(data.id), url: data.invoice_url };
}

function sortObject(value) {
  if (Array.isArray(value)) return value.map(sortObject);
  if (value && typeof value === "object") {
    return Object.keys(value)
      .sort()
      .reduce((result, key) => {
        result[key] = sortObject(value[key]);
        return result;
      }, {});
  }
  return value;
}

// Checks the x-nowpayments-sig header so nobody can fake a "paid" message.
export function verifyWebhook(body, signature) {
  const secret = process.env.NOWPAYMENTS_IPN_SECRET;
  if (!secret || !signature) return false;
  const expected = crypto.createHmac("sha512", secret).update(JSON.stringify(sortObject(body))).digest("hex");
  const a = Buffer.from(expected);
  const b = Buffer.from(String(signature));
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}
