-- Tuff Boosts database
-- Paste this whole file into Supabase > SQL Editor > New query, then press Run.
-- All money is stored in US cents (100 = $1.00).

-- ---------------------------------------------------------------
-- Tables
-- ---------------------------------------------------------------

create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  username text not null,
  is_admin boolean not null default false,
  spent_cents bigint not null default 0,
  created_at timestamptz not null default now(),
  constraint username_format check (username ~ '^[A-Za-z0-9_]{3,20}$')
);
create unique index if not exists profiles_username_lower on public.profiles (lower(username));

create table if not exists public.settings (
  id int primary key default 1 check (id = 1),
  discord_url text not null default 'https://discord.gg/TuffBoosts',
  boosts_sold int not null default 0,
  business_name text not null default '',
  contact_email text not null default '',
  business_address text not null default '',
  coming_soon jsonb not null default '{"boosts": false, "nitro": true}'::jsonb
);
insert into public.settings (id) values (1) on conflict (id) do nothing;

create table if not exists public.products (
  id uuid primary key default gen_random_uuid(),
  name text not null check (char_length(name) between 1 and 80),
  category text not null check (category in ('boosts', 'nitro')),
  price_cents int not null check (price_cents > 0),
  sold int not null default 0 check (sold >= 0),
  description text not null default '' check (char_length(description) <= 240),
  active boolean not null default true,
  created_at timestamptz not null default now()
);

create table if not exists public.discount_codes (
  code text primary key check (code ~ '^[A-Z0-9_-]{2,30}$'),
  kind text not null check (kind in ('percent', 'fixed')),
  amount int not null check (amount > 0),          -- percent: 1-100, fixed: cents
  min_cents int not null default 0 check (min_cents >= 0),
  max_uses int check (max_uses is null or max_uses > 0),
  uses int not null default 0 check (uses >= 0),
  expires_at timestamptz,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  constraint percent_range check (kind <> 'percent' or amount <= 100)
);

create table if not exists public.orders (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  product_id uuid references public.products(id) on delete set null,
  product_name text not null,
  category text not null,
  server_link text not null,
  subtotal_cents int not null,
  discount_code text,
  discount_cents int not null default 0,
  credit_used_cents int not null default 0,
  total_cents int not null,
  pay_currency text not null,
  status text not null default 'awaiting_payment'
    check (status in ('awaiting_payment', 'paid', 'completed', 'cancelled', 'refunded')),
  invoice_id text,
  invoice_url text,
  payment_note text,
  created_at timestamptz not null default now(),
  paid_at timestamptz,
  refunded_at timestamptz
);
create index if not exists orders_user on public.orders (user_id, created_at desc);
create index if not exists orders_status on public.orders (status, created_at desc);

create table if not exists public.credit_ledger (
  id bigint generated always as identity primary key,
  user_id uuid not null references public.profiles(id) on delete cascade,
  amount_cents int not null,
  reason text not null check (reason in ('reward', 'reward_reversed', 'refund', 'order', 'order_cancelled', 'adjustment')),
  order_id uuid references public.orders(id) on delete set null,
  note text not null default '',
  created_at timestamptz not null default now()
);
create index if not exists credit_ledger_user on public.credit_ledger (user_id, created_at desc);

create table if not exists public.chats (
  user_id uuid primary key references public.profiles(id) on delete cascade,
  last_text text not null default '',
  unread boolean not null default false,
  updated_at timestamptz not null default now()
);

create table if not exists public.chat_messages (
  id bigint generated always as identity primary key,
  user_id uuid not null references public.profiles(id) on delete cascade,
  sender text not null check (sender in ('customer', 'admin')),
  body text not null check (char_length(body) between 1 and 1000),
  created_at timestamptz not null default now()
);
create index if not exists chat_messages_user on public.chat_messages (user_id, created_at);

-- ---------------------------------------------------------------
-- Helpers
-- ---------------------------------------------------------------

create or replace function public.is_admin() returns boolean
language sql stable security definer set search_path = public as $$
  select coalesce((select is_admin from public.profiles where id = auth.uid()), false);
$$;

create or replace function public.credit_balance(p_user uuid) returns bigint
language sql stable security definer set search_path = public as $$
  select coalesce(sum(amount_cents), 0)::bigint from public.credit_ledger where user_id = p_user;
$$;

create or replace function public.username_available(p_username text) returns boolean
language sql stable security definer set search_path = public as $$
  select not exists (select 1 from public.profiles where lower(username) = lower(trim(p_username)));
$$;

-- Create a profile when someone signs up. The username comes from sign-up metadata.
create or replace function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (id, username)
  values (new.id, trim(new.raw_user_meta_data->>'username'));
  return new;
end;
$$;
drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created after insert on auth.users
  for each row execute function public.handle_new_user();

-- Keep the chat inbox row up to date.
create or replace function public.touch_chat() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into public.chats (user_id, last_text, unread, updated_at)
  values (new.user_id, left(new.body, 140), new.sender = 'customer', new.created_at)
  on conflict (user_id) do update
    set last_text = excluded.last_text, unread = excluded.unread, updated_at = excluded.updated_at;
  return new;
end;
$$;
drop trigger if exists chat_message_inserted on public.chat_messages;
create trigger chat_message_inserted after insert on public.chat_messages
  for each row execute function public.touch_chat();

-- ---------------------------------------------------------------
-- Pricing: works out discount, credit and total for one product
-- ---------------------------------------------------------------

create or replace function public.internal_quote(p_user uuid, p_product uuid, p_code text, p_use_credit boolean)
returns jsonb language plpgsql stable security definer set search_path = public as $$
declare
  prod public.products%rowtype;
  dc public.discount_codes%rowtype;
  v_sub bigint;
  v_disc bigint := 0;
  v_bal bigint;
  v_credit bigint := 0;
  v_code text := null;
begin
  select * into prod from public.products where id = p_product and active;
  if not found then
    return jsonb_build_object('error', 'This product isn''t available.');
  end if;
  if exists (select 1 from public.settings where id = 1 and coalesce((coming_soon->>prod.category)::boolean, false)) then
    return jsonb_build_object('error', 'This product is coming soon.');
  end if;

  v_sub := prod.price_cents;

  if coalesce(trim(p_code), '') <> '' then
    select * into dc from public.discount_codes where code = upper(trim(p_code));
    if not found or not dc.active then
      return jsonb_build_object('error', 'That discount code isn''t valid.');
    end if;
    if dc.expires_at is not null and dc.expires_at < now() then
      return jsonb_build_object('error', 'That discount code has expired.');
    end if;
    if dc.max_uses is not null and dc.uses >= dc.max_uses then
      return jsonb_build_object('error', 'That discount code has been used up.');
    end if;
    if v_sub < dc.min_cents then
      return jsonb_build_object('error', 'That code needs an order of at least $' || to_char(dc.min_cents / 100.0, 'FM999990.00') || '.');
    end if;
    if dc.kind = 'percent' then
      v_disc := floor(v_sub * dc.amount / 100.0);
    else
      v_disc := least(v_sub, dc.amount);
    end if;
    v_code := dc.code;
  end if;

  v_bal := greatest(0, public.credit_balance(p_user));
  if p_use_credit then
    v_credit := least(v_bal, v_sub - v_disc);
  end if;

  return jsonb_build_object(
    'product_name', prod.name,
    'category', prod.category,
    'subtotal', v_sub,
    'discount', v_disc,
    'code', v_code,
    'balance', v_bal,
    'credit', v_credit,
    'total', v_sub - v_disc - v_credit
  );
end;
$$;

-- Called by signed-in customers from the checkout page (preview only, changes nothing).
create or replace function public.quote_order(p_product uuid, p_code text, p_use_credit boolean)
returns jsonb language plpgsql stable security definer set search_path = public as $$
begin
  if auth.uid() is null then
    return jsonb_build_object('error', 'Log in to check out.');
  end if;
  return public.internal_quote(auth.uid(), p_product, p_code, p_use_credit);
end;
$$;

-- ---------------------------------------------------------------
-- Order lifecycle (server only)
-- ---------------------------------------------------------------

create or replace function public.internal_mark_paid(p_order uuid, p_invoice text default null)
returns void language plpgsql security definer set search_path = public as $$
declare
  o public.orders%rowtype;
  v_before bigint;
  v_after bigint;
  v_reward bigint;
begin
  select * into o from public.orders where id = p_order for update;
  if not found or o.status in ('paid', 'completed', 'refunded') then
    return;
  end if;

  -- Paid after being cancelled: take the credit and code use again.
  if o.status = 'cancelled' then
    if o.credit_used_cents > 0 then
      insert into public.credit_ledger (user_id, amount_cents, reason, order_id, note)
      values (o.user_id, -o.credit_used_cents, 'order', o.id, 'Credit used again: order was paid after it was cancelled');
    end if;
    if o.discount_code is not null then
      update public.discount_codes set uses = uses + 1 where code = o.discount_code;
    end if;
  end if;

  update public.orders
     set status = 'paid', paid_at = now(), invoice_id = coalesce(p_invoice, invoice_id)
   where id = o.id;

  select spent_cents into v_before from public.profiles where id = o.user_id for update;
  v_after := v_before + o.total_cents;
  update public.profiles set spent_cents = v_after where id = o.user_id;

  -- Rewards: $2 credit for every $20 spent.
  v_reward := (v_after / 2000 - v_before / 2000) * 200;
  if v_reward > 0 then
    insert into public.credit_ledger (user_id, amount_cents, reason, order_id, note)
    values (o.user_id, v_reward, 'reward', o.id, 'Rewards: $2 for every $20 spent');
  end if;

  update public.products set sold = sold + 1 where id = o.product_id;
end;
$$;

create or replace function public.internal_cancel_order(p_order uuid)
returns void language plpgsql security definer set search_path = public as $$
declare
  o public.orders%rowtype;
begin
  select * into o from public.orders where id = p_order for update;
  if not found or o.status <> 'awaiting_payment' then
    return;
  end if;
  update public.orders set status = 'cancelled' where id = o.id;
  if o.credit_used_cents > 0 then
    insert into public.credit_ledger (user_id, amount_cents, reason, order_id, note)
    values (o.user_id, o.credit_used_cents, 'order_cancelled', o.id, 'Credit returned: order cancelled');
  end if;
  if o.discount_code is not null then
    update public.discount_codes set uses = greatest(0, uses - 1) where code = o.discount_code;
  end if;
end;
$$;

-- Creates an order. Locks the customer so credit can't be spent twice at once.
create or replace function public.place_order(
  p_user uuid, p_product uuid, p_server text, p_code text, p_use_credit boolean, p_currency text
) returns jsonb language plpgsql security definer set search_path = public as $$
declare
  q jsonb;
  v_id uuid;
begin
  perform 1 from public.profiles where id = p_user for update;
  if not found then
    return jsonb_build_object('error', 'Your account wasn''t found. Log out and back in.');
  end if;
  if char_length(coalesce(trim(p_server), '')) not between 1 and 200 then
    return jsonb_build_object('error', 'Enter your server link.');
  end if;

  q := public.internal_quote(p_user, p_product, p_code, p_use_credit);
  if q ? 'error' then
    return q;
  end if;

  insert into public.orders (
    user_id, product_id, product_name, category, server_link, subtotal_cents, discount_code,
    discount_cents, credit_used_cents, total_cents, pay_currency
  ) values (
    p_user, p_product, q->>'product_name', q->>'category', trim(p_server), (q->>'subtotal')::int, q->>'code',
    (q->>'discount')::int, (q->>'credit')::int, (q->>'total')::int,
    case when (q->>'total')::int = 0 then 'credit' else p_currency end
  ) returning id into v_id;

  if (q->>'credit')::int > 0 then
    insert into public.credit_ledger (user_id, amount_cents, reason, order_id, note)
    values (p_user, -(q->>'credit')::int, 'order', v_id, 'Credit used on order');
  end if;
  if q->>'code' is not null then
    update public.discount_codes set uses = uses + 1 where code = q->>'code';
  end if;

  if (q->>'total')::int = 0 then
    perform public.internal_mark_paid(v_id, null);
  end if;

  return jsonb_build_object('order_id', v_id, 'total', (q->>'total')::int);
end;
$$;

-- ---------------------------------------------------------------
-- Customer actions
-- ---------------------------------------------------------------

create or replace function public.cancel_my_order(p_order uuid)
returns void language plpgsql security definer set search_path = public as $$
begin
  if not exists (select 1 from public.orders where id = p_order and user_id = auth.uid()) then
    raise exception 'Order not found';
  end if;
  perform public.internal_cancel_order(p_order);
end;
$$;

-- ---------------------------------------------------------------
-- Admin actions (each checks the caller is an admin)
-- ---------------------------------------------------------------

create or replace function public.admin_set_order_status(p_order uuid, p_status text)
returns void language plpgsql security definer set search_path = public as $$
begin
  if not public.is_admin() then raise exception 'Admins only'; end if;
  if p_status = 'paid' then
    perform public.internal_mark_paid(p_order, null);
  elsif p_status = 'cancelled' then
    perform public.internal_cancel_order(p_order);
  elsif p_status = 'completed' then
    update public.orders set status = 'completed' where id = p_order and status = 'paid';
  else
    raise exception 'Unknown status';
  end if;
end;
$$;

-- Refund a paid order as account credit: the amount paid plus any credit used.
create or replace function public.admin_refund_to_credit(p_order uuid)
returns void language plpgsql security definer set search_path = public as $$
declare
  o public.orders%rowtype;
  v_before bigint;
  v_after bigint;
  v_reversed bigint;
begin
  if not public.is_admin() then raise exception 'Admins only'; end if;
  select * into o from public.orders where id = p_order for update;
  if not found or o.status not in ('paid', 'completed') then
    raise exception 'Only paid or completed orders can be refunded';
  end if;

  update public.orders set status = 'refunded', refunded_at = now() where id = o.id;

  if o.total_cents + o.credit_used_cents > 0 then
    insert into public.credit_ledger (user_id, amount_cents, reason, order_id, note)
    values (o.user_id, o.total_cents + o.credit_used_cents, 'refund', o.id, 'Refund for ' || o.product_name);
  end if;

  -- Take back the rewards this order earned.
  select spent_cents into v_before from public.profiles where id = o.user_id for update;
  v_after := greatest(0, v_before - o.total_cents);
  update public.profiles set spent_cents = v_after where id = o.user_id;
  v_reversed := (v_before / 2000 - v_after / 2000) * 200;
  if v_reversed > 0 then
    insert into public.credit_ledger (user_id, amount_cents, reason, order_id, note)
    values (o.user_id, -v_reversed, 'reward_reversed', o.id, 'Rewards removed for refunded order');
  end if;

  update public.products set sold = greatest(0, sold - 1) where id = o.product_id;
end;
$$;

create or replace function public.admin_adjust_credit(p_user uuid, p_cents int, p_note text)
returns void language plpgsql security definer set search_path = public as $$
begin
  if not public.is_admin() then raise exception 'Admins only'; end if;
  if p_cents = 0 then raise exception 'Enter an amount'; end if;
  insert into public.credit_ledger (user_id, amount_cents, reason, note)
  values (p_user, p_cents, 'adjustment', left(coalesce(p_note, ''), 200));
end;
$$;

-- Everyone who signed up: username, join date and totals only (no emails or passwords).
create or replace function public.admin_customers()
returns table (id uuid, username text, created_at timestamptz, spent_cents bigint, credit_cents bigint, order_count bigint)
language plpgsql stable security definer set search_path = public as $$
begin
  if not public.is_admin() then raise exception 'Admins only'; end if;
  return query
    select p.id, p.username, p.created_at, p.spent_cents,
           public.credit_balance(p.id),
           (select count(*) from public.orders o where o.user_id = p.id and o.status in ('paid', 'completed', 'refunded'))
      from public.profiles p
     order by p.created_at desc;
end;
$$;

-- ---------------------------------------------------------------
-- Who may run what
-- ---------------------------------------------------------------

revoke execute on all functions in schema public from public, anon, authenticated;

grant execute on function public.is_admin() to anon, authenticated;
grant execute on function public.username_available(text) to anon, authenticated;
grant execute on function public.quote_order(uuid, text, boolean) to authenticated;
grant execute on function public.cancel_my_order(uuid) to authenticated;
grant execute on function public.admin_set_order_status(uuid, text) to authenticated;
grant execute on function public.admin_refund_to_credit(uuid) to authenticated;
grant execute on function public.admin_adjust_credit(uuid, int, text) to authenticated;
grant execute on function public.admin_customers() to authenticated;

grant execute on function public.place_order(uuid, uuid, text, text, boolean, text) to service_role;
grant execute on function public.internal_mark_paid(uuid, text) to service_role;
grant execute on function public.internal_cancel_order(uuid) to service_role;

-- ---------------------------------------------------------------
-- Row security
-- ---------------------------------------------------------------

alter table public.profiles enable row level security;
alter table public.settings enable row level security;
alter table public.products enable row level security;
alter table public.discount_codes enable row level security;
alter table public.orders enable row level security;
alter table public.credit_ledger enable row level security;
alter table public.chats enable row level security;
alter table public.chat_messages enable row level security;

drop policy if exists "own profile" on public.profiles;
create policy "own profile" on public.profiles for select using (id = auth.uid() or public.is_admin());

drop policy if exists "read settings" on public.settings;
create policy "read settings" on public.settings for select using (true);
drop policy if exists "admin settings" on public.settings;
create policy "admin settings" on public.settings for update using (public.is_admin()) with check (public.is_admin());

drop policy if exists "read products" on public.products;
create policy "read products" on public.products for select using (active or public.is_admin());
drop policy if exists "admin products" on public.products;
create policy "admin products" on public.products for all using (public.is_admin()) with check (public.is_admin());

drop policy if exists "admin codes" on public.discount_codes;
create policy "admin codes" on public.discount_codes for all using (public.is_admin()) with check (public.is_admin());

drop policy if exists "own orders" on public.orders;
create policy "own orders" on public.orders for select using (user_id = auth.uid() or public.is_admin());

drop policy if exists "own credit" on public.credit_ledger;
create policy "own credit" on public.credit_ledger for select using (user_id = auth.uid() or public.is_admin());

drop policy if exists "own chat" on public.chats;
create policy "own chat" on public.chats for select using (user_id = auth.uid() or public.is_admin());
drop policy if exists "admin chat update" on public.chats;
create policy "admin chat update" on public.chats for update using (public.is_admin()) with check (public.is_admin());

drop policy if exists "read messages" on public.chat_messages;
create policy "read messages" on public.chat_messages for select using (user_id = auth.uid() or public.is_admin());
drop policy if exists "send messages" on public.chat_messages;
create policy "send messages" on public.chat_messages for insert with check (
  (sender = 'customer' and user_id = auth.uid()) or (sender = 'admin' and public.is_admin())
);

-- Live chat updates
do $$
begin
  begin alter publication supabase_realtime add table public.chat_messages; exception when duplicate_object then null; end;
  begin alter publication supabase_realtime add table public.chats; exception when duplicate_object then null; end;
end $$;

-- Product images (safe to run more than once)
alter table public.products add column if not exists image_url text not null default '';

insert into storage.buckets (id, name, public) values ('product-images', 'product-images', true)
on conflict (id) do nothing;

drop policy if exists "admins upload product images" on storage.objects;
create policy "admins upload product images" on storage.objects for insert to authenticated
  with check (bucket_id = 'product-images' and public.is_admin());
drop policy if exists "admins delete product images" on storage.objects;
create policy "admins delete product images" on storage.objects for delete to authenticated
  using (bucket_id = 'product-images' and public.is_admin());

-- Order numbers and receipts
alter table public.orders add column if not exists order_number bigint generated always as identity (start with 1001);
alter table public.orders add column if not exists receipt_sent_at timestamptz;
create unique index if not exists orders_order_number on public.orders (order_number);
