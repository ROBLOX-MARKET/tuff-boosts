# Tuff Boosts — setup guide

Everything is in US dollars. Customers pay in USDT (BNB Smart Chain), Bitcoin or Litecoin.

**What's included:** accounts with usernames, login and password reset · order history · refunds as account credit · $2 credit for every $20 spent · discount codes · crypto checkout · live chat · admin page (orders, customers, products, codes, site settings).

You'll make four free accounts: **Supabase**, **NOWPayments**, **GitHub** and **Vercel**. Takes about an hour.

---

## 1. Supabase (accounts and data)

1. Go to supabase.com, sign up, and click **New project**. Pick a name and a strong database password, and choose a region near your customers.
2. When it's ready, open **SQL Editor → New query**. Open `supabase/schema.sql` from this folder, copy everything, paste it in, and click **Run**. You should see "Success".
3. Open **Project Settings → API** and keep this tab open. You'll need:
   - Project URL
   - `anon` `public` key
   - `service_role` key (secret: never share it or put it in GitHub)

## 2. NOWPayments (crypto payments)

1. Sign up at nowpayments.io.
2. In **Settings → Payout wallets**, add the wallets you want to be paid into.
3. In **Settings → Coins**, turn on **USDT (BSC / BEP20)**, **BTC** and **LTC**.
4. In **Settings → Payments**, create an **API key** and copy it.
5. In the **IPN** settings, generate an **IPN secret key** and copy it.

## 3. GitHub (stores the code)

1. Sign up at github.com and click **New repository**. Name it `tuff-boosts` and set it to **Private**.
2. Click **uploading an existing file**, drag in everything inside this folder, and click **Commit changes**.
   Never upload a file called `.env`.

## 4. Vercel (puts the site online)

1. Sign up at vercel.com with your GitHub account.
2. Click **Add New → Project** and import `tuff-boosts`.
3. Before clicking Deploy, open **Environment Variables** and add these six (see `.env.example`):

| Name | Value |
|---|---|
| `NEXT_PUBLIC_SUPABASE_URL` | Supabase Project URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase anon public key |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service_role key |
| `NOWPAYMENTS_API_KEY` | NOWPayments API key |
| `NOWPAYMENTS_IPN_SECRET` | NOWPayments IPN secret |
| `NEXT_PUBLIC_SITE_URL` | Your site address, e.g. `https://tuffboosts.com` (use the vercel.app address until your domain is connected) |

4. Click **Deploy**. After a minute or two you get a working link.

## 5. Your domain

1. Buy a domain (Namecheap, Cloudflare or Porkbun).
2. In Vercel: **Project → Settings → Domains → Add**, type your domain, and follow the instructions to add the records at your domain provider.
3. Once it works, update `NEXT_PUBLIC_SITE_URL` in Vercel to your domain and click **Redeploy**.

## 6. Connect logins to your site

In Supabase, open **Authentication → URL Configuration**:

- **Site URL:** your domain, e.g. `https://tuffboosts.com`
- **Redirect URLs:** add `https://tuffboosts.com/**` (and your vercel.app address with `/**` while testing)

This makes the confirm-email and reset-password links open your site.

**Before launch:** Supabase's built-in email only sends a few emails per hour. Set up your own email sender under **Authentication → Emails → SMTP settings** (Resend or Brevo have free plans) so sign-up and password reset emails always arrive.

## 7. Make yourself the admin

1. Sign up on your live site like a customer and confirm your email.
2. In Supabase **SQL Editor**, run (with your username):

```sql
update public.profiles set is_admin = true where lower(username) = lower('YourUsername');
```

3. Refresh the site. **Admin** appears in the menu, and the chat button opens your customer inbox.

## 8. Test it

1. In Admin → Products, add a product (price in dollars).
2. In Admin → Discount codes, add a code like `TEST10`.
3. Log in with a second account, buy the product with the code, and pay a small amount.
4. Watch the order move to **Paid** in Admin → Orders (blockchain confirmations can take 10–60 minutes for BTC).

---

## How it works

- **Orders:** the customer places an order, gets a NOWPayments payment page, and when NOWPayments confirms the payment the order becomes **Paid** automatically. You then fulfil it and click **Mark completed**.
- **Rewards:** each time a customer's total spending passes another $20, they get $2 credit automatically.
- **Refunds:** click **Refund to credit** on a paid order. The customer gets back what they paid plus any credit they used, and the rewards that order earned are removed.
- **Discount codes:** percentage or dollar amount off, with optional minimum order, use limit and expiry date.
- **Credit:** customers can tick "Use my credit" at checkout. If credit covers everything, no payment is needed. You can also add or remove credit by hand in Admin → Customers.
- **Customers list:** shows usernames, join dates, orders, spending and credit only. Emails and passwords are never shown.
- **Unpaid orders:** customers can cancel them from their account, which returns any credit used. Expired payments cancel automatically.
- **Underpayments:** if someone sends too little, the order stays "Awaiting payment" with a note. Sort it out in live chat, then click **Mark paid** if they top it up.

## Changing the gateway

All NOWPayments code is in `lib/nowpayments.js` and `app/api/webhooks/nowpayments/route.js`. To use another gateway, only those two files need changing.
