import Link from "next/link";
import BusinessDetails from "@/components/BusinessDetails";

export const metadata = { title: "Privacy Policy · Tuff Boosts" };

export default function Privacy() {
  return (
    <main className="wrap">
      <article className="terms">
        <h1>Privacy Policy</h1>
        <p>Last updated 8 November 2026.</p>

        <h2>What we collect</h2>
        <p>Your username, email and password when you sign up (passwords are stored encrypted and we can&apos;t see them), your orders and server links, your credit history, and your messages if you use live chat.</p>

        <h2>How we use it</h2>
        <p>To run your account, complete orders, track your rewards and credit, provide support, and meet our legal duties.</p>

        <h2>Who handles it</h2>
        <p>Supabase stores account and order data for us. NOWPayments processes crypto payments under its own privacy policy. Vercel hosts the website. We don&apos;t sell your personal details.</p>

        <h2>How long we keep it</h2>
        <p>We keep order records for as long as the law requires. You can ask us to delete your account at any time.</p>

        <h2>Your rights</h2>
        <p>You can ask to see, correct or delete your personal details by contacting us. You can also complain to the Information Commissioner&apos;s Office.</p>

        <h2>Contact</h2>
        <BusinessDetails />
        <p><Link href="/">Back to the store</Link></p>
      </article>
    </main>
  );
}
