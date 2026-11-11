import { NextResponse } from "next/server";
import { supabaseAdmin, userFromRequest } from "@/lib/supabaseAdmin";
import { sendReceipt } from "@/lib/email";
import { notifyNewOrder } from "@/lib/discord";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Admins: send (or resend) a receipt for a paid order.
export async function POST(request) {
  const user = await userFromRequest(request);
  if (!user) return NextResponse.json({ error: "Log in first." }, { status: 401 });
  const { data: profile } = await supabaseAdmin().from("profiles").select("is_admin").eq("id", user.id).maybeSingle();
  if (!profile?.is_admin) return NextResponse.json({ error: "Admins only." }, { status: 403 });

  const body = await request.json().catch(() => ({}));
  const kind = body.kind === "completed" ? "completed" : "receipt";
  const result = await sendReceipt(String(body.orderId || ""), { force: Boolean(body.resend), kind });
  if (kind === "receipt") await notifyNewOrder(String(body.orderId || ""));
  return NextResponse.json(result, { status: result.sent || result.reason === "Already sent." ? 200 : 400 });
}
