// All money is stored as US cents.
export function usd(cents) {
  const value = (Number(cents) || 0) / 100;
  return value.toLocaleString("en-US", { style: "currency", currency: "USD" });
}

// "12.5" or "$12.50" -> 1250. Returns null if it isn't a valid amount.
export function toCents(text) {
  const cleaned = String(text || "").replace(/[$,\s]/g, "");
  if (!/^\d+(\.\d{1,2})?$/.test(cleaned)) return null;
  return Math.round(Number(cleaned) * 100);
}

export const REWARD_STEP_CENTS = 2000; // spend $20
export const REWARD_CENTS = 200; // get $2

export const CURRENCIES = [
  { id: "usdtbsc", label: "USDT (BNB Smart Chain)" },
  { id: "btc", label: "Bitcoin (BTC)" },
  { id: "ltc", label: "Litecoin (LTC)" }
];

export const CATEGORIES = [
  { id: "boosts", label: "Server Boosts", first: "Server", second: "Boosts" },
  { id: "nitro", label: "Discord Nitro", first: "Discord", second: "Nitro" }
];

export const STATUS_LABELS = {
  awaiting_payment: "Awaiting payment",
  paid: "Paid, in progress",
  completed: "Completed",
  cancelled: "Cancelled",
  refunded: "Refunded to credit"
};

export function orderLabel(order) {
  return order.order_number ? "#TB-" + order.order_number : "#" + String(order.id).slice(0, 8).toUpperCase();
}

export function shortDate(value) {
  return new Date(value).toLocaleDateString("en-US", { day: "numeric", month: "short", year: "numeric" });
}
