import Link from "next/link";
import BusinessDetails from "@/components/BusinessDetails";

export const metadata = { title: "Terms of Service · Tuff Boosts" };

export default function Terms() {
  return (
    <main className="wrap">
      <article className="terms">
        <h1>Terms of Service</h1>
        <p>Last updated 8 November 2026. By creating an account or ordering from Tuff Boosts, you agree to these terms.</p>

        <h2>Your account</h2>
        <p>Keep your password private. You&apos;re responsible for orders placed from your account. You can reset your password from the login page at any time.</p>

        <h2>Prices and payment</h2>
        <p>All prices are in US dollars. We accept USDT on BNB Smart Chain, Bitcoin and Litecoin through our payment provider, NOWPayments. Send the exact amount shown on the payment page before it expires. Network fees charged by your wallet are yours to pay.</p>

        <h2>Your order details</h2>
        <p>Check your server link before paying. If you give us the wrong server, we can&apos;t refund your order unless the law requires us to.</p>

        <h2>Discount codes</h2>
        <p>Codes can&apos;t be exchanged for cash, can be used once per order, and may have limits or expiry dates. We may withdraw a code at any time, but this won&apos;t affect orders already placed.</p>

        <h2>Rewards and credit</h2>
        <p>For every $20 you spend, we add $2 credit to your account. Credit can be used on future orders, can&apos;t be exchanged for cash, and can&apos;t be moved to another account. If an order is refunded, any rewards it earned are removed.</p>

        <h2>Cancellations and refunds</h2>
        <p>You can cancel an order that hasn&apos;t been paid yet from your account page. If a paid order is refunded, we add the full amount to your account as credit, including any credit you used on it.</p>
        <p>UK customers can usually cancel within 14 days without giving a reason. If you ask us to start straight away, you may have to pay for what we&apos;ve already provided, and you may lose the right to cancel once the order is complete if you agreed to this before we started. If the law entitles you to your money back rather than credit, contact us and we&apos;ll send it to a wallet address you give us.</p>

        <h2>Reporting a problem</h2>
        <p>Contact us within 7 days of receiving your order if anything goes wrong so we can look into it. After 7 days, we may be unable to help unless the law requires us to.</p>

        <h2>Discord&apos;s rules</h2>
        <p>Tuff Boosts isn&apos;t connected to or endorsed by Discord. Discord may remove boosts or Nitro, or take action on servers and accounts, under its own rules.</p>

        <h2>Your legal rights</h2>
        <p>Nothing in these terms removes any rights you have under the law.</p>

        <h2>Business details</h2>
        <BusinessDetails />
        <p><Link href="/">Back to the store</Link></p>
      </article>
    </main>
  );
}
