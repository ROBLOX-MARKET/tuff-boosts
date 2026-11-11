import { NextResponse } from "next/server";
import { supabaseAdmin } from "@/lib/supabaseAdmin";
import { verifyWebhook } from "@/lib/nowpayments";
import { sendReceipt } from "@/lib/email";
import { notifyNewOrder } from "@/lib/discord";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// NOWPayments calls this whenever a payment changes status.
export async function POST(request) {
  const raw = await request.text();
  let body;
  try {
    body = JSON.parse(raw);
  } catch {
    return NextResponse.json({ error: "Bad request" }, { status: 400 });
  }
  if (!verifyWebhook(body, request.headers.get("x-nowpayments-sig"))) {
    return NextResponse.json({ error: "Bad signature" }, { status: 401 });
  }

  const db = supabaseAdmin();
  const orderId = String(body.order_id || "");
  const { data: order } = await db.from("orders").select("*").eq("id", orderId).maybeSingle();
  if (!order) return NextResponse.json({ ok: true });

  const status = String(body.payment_status || "");
  const reference = String(body.invoice_id || body.payment_id || "");

  if (status === "finished") {
    const paidEnough =
      String(body.price_currency || "").toLowerCase() === "usd" &&
      Number(body.price_amount) * 100 >= order.total_cents - 1;
    if (paidEnough) {
      await db.rpc("internal_mark_paid", { p_order: order.id, p_invoice: reference });
      await Promise.all([sendReceipt(order.id), notifyNewOrder(order.id)]);
    } else {
      await db.from("orders").update({ payment_note: "Payment amount didn't match. Check NOWPayments." }).eq("id", order.id);
    }
  } else if (status === "partially_paid") {
    await db
      .from("orders")
      .update({ payment_note: "Partly paid: " + body.actually_paid + " of " + body.pay_amount + " " + String(body.pay_currency || "").toUpperCase() })
      .eq("id", order.id);
  } else if (status === "expired" || status === "failed") {
    await db.rpc("internal_cancel_order", { p_order: order.id });
  }

  return NextResponse.json({ ok: true });
}
