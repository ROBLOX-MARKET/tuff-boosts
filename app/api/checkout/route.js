import { NextResponse } from "next/server";
import { supabaseAdmin, userFromRequest } from "@/lib/supabaseAdmin";
import { createInvoice } from "@/lib/nowpayments";
import { CURRENCIES } from "@/lib/money";
import { sendReceipt } from "@/lib/email";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request) {
  const user = await userFromRequest(request);
  if (!user) return NextResponse.json({ error: "Log in to check out." }, { status: 401 });

  const body = await request.json().catch(() => ({}));
  const productId = String(body.productId || "");
  const serverLink = String(body.serverLink || "").trim();
  const code = String(body.code || "").trim();
  const useCredit = Boolean(body.useCredit);
  const currency = String(body.currency || "");
  const quantity = Math.max(1, Math.min(100, Math.floor(Number(body.quantity) || 1)));

  if (!CURRENCIES.some((item) => item.id === currency)) {
    return NextResponse.json({ error: "Choose how you want to pay." }, { status: 400 });
  }

  const db = supabaseAdmin();
  const { data, error } = await db.rpc("place_order_qty", {
    p_user: user.id,
    p_product: productId,
    p_server: serverLink,
    p_code: code,
    p_use_credit: useCredit,
    p_currency: currency,
    p_qty: quantity
  });
  if (error && String(error.message).includes("OUT_OF_STOCK")) {
    return NextResponse.json({ error: "Sorry, those just sold out. Try a smaller amount." }, { status: 409 });
  }
  if (error) return NextResponse.json({ error: "Your order couldn't be placed. Please try again." }, { status: 500 });
  if (data.error) return NextResponse.json({ error: data.error }, { status: 400 });

  const orderId = data.order_id;
  if (data.total === 0) {
    await sendReceipt(orderId);
    return NextResponse.json({ paid: true, orderId });
  }

  try {
    const { data: order } = await db.from("orders").select("product_name").eq("id", orderId).single();
    const invoice = await createInvoice({
      orderId,
      totalCents: data.total,
      currency,
      description: "Tuff Boosts: " + (order?.product_name || "order") + (quantity > 1 ? " x" + quantity : "")
    });
    await db.from("orders").update({ invoice_id: invoice.id, invoice_url: invoice.url }).eq("id", orderId);
    return NextResponse.json({ url: invoice.url, orderId });
  } catch (invoiceError) {
    // Give back any credit and code use if the payment page couldn't be made.
    await db.rpc("internal_cancel_order", { p_order: orderId });
    return NextResponse.json(
      { error: invoiceError.message + " Your credit hasn't been used. Try another coin, or contact us." },
      { status: 502 }
    );
  }
}
