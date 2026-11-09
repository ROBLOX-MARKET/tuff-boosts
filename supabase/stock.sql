-- Stock and automatic code delivery (e.g. Nitro gift links)

alter table public.products add column if not exists stock_tracked boolean not null default false;
alter table public.orders add column if not exists quantity int not null default 1;
do $$ begin
  alter table public.orders add constraint orders_quantity_range check (quantity between 1 and 100);
exception when duplicate_object then null; end $$;

create table if not exists public.product_codes (
  id bigint generated always as identity primary key,
  product_id uuid not null references public.products(id) on delete cascade,
  code text not null check (char_length(code) between 1 and 500),
  status text not null default 'available' check (status in ('available', 'reserved', 'delivered')),
  order_id uuid references public.orders(id) on delete set null,
  created_at timestamptz not null default now(),
  delivered_at timestamptz
);
create index if not exists product_codes_available on public.product_codes (product_id, status, id);
create index if not exists product_codes_order on public.product_codes (order_id);
create unique index if not exists product_codes_unique on public.product_codes (product_id, code);

alter table public.product_codes enable row level security;
do $$ begin
  create policy "admins manage codes" on public.product_codes for all using (public.is_admin()) with check (public.is_admin());
exception when duplicate_object then null; end $$;
do $$ begin
  create policy "customers see delivered codes" on public.product_codes for select using (
    status = 'delivered' and exists (select 1 from public.orders o where o.id = order_id and o.user_id = auth.uid())
  );
exception when duplicate_object then null; end $$;

-- How many codes are left for each stock-tracked product (safe for anyone to read).
create or replace function public.stock_counts()
returns table (product_id uuid, available bigint)
language sql stable security definer set search_path = public as $$
  select p.id, (select count(*) from public.product_codes c where c.product_id = p.id and c.status = 'available')
    from public.products p where p.stock_tracked;
$$;

-- Holds codes for an order. Returns how many it managed to hold.
create or replace function public.internal_reserve_codes(p_order uuid, p_product uuid, p_qty int)
returns int language plpgsql security definer set search_path = public as $$
declare v_count int;
begin
  with picked as (
    select id from public.product_codes
     where product_id = p_product and status = 'available'
     order by id
     limit p_qty
     for update skip locked
  )
  update public.product_codes c set status = 'reserved', order_id = p_order
    from picked where c.id = picked.id;
  get diagnostics v_count = row_count;
  return v_count;
end;
$$;


create or replace function public.internal_quote_qty(p_user uuid, p_product uuid, p_code text, p_use_credit boolean, p_qty int)
returns jsonb language plpgsql stable security definer set search_path = public as $$
declare
  prod public.products%rowtype;
  dc public.discount_codes%rowtype;
  v_qty int;
  v_stock bigint := null;
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

  if prod.stock_tracked then
    v_qty := coalesce(p_qty, 1);
    if v_qty < 1 or v_qty > 100 then
      return jsonb_build_object('error', 'Choose a quantity from 1 to 100.');
    end if;
    select count(*) into v_stock from public.product_codes where product_id = prod.id and status = 'available';
    if v_stock = 0 then
      return jsonb_build_object('error', 'This product is out of stock.', 'stock', 0);
    end if;
    if v_qty > v_stock then
      return jsonb_build_object('error', 'Only ' || v_stock || ' left in stock.', 'stock', v_stock);
    end if;
  else
    v_qty := 1;
  end if;

  v_sub := prod.price_cents * v_qty;

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
    'stock_tracked', prod.stock_tracked,
    'stock', v_stock,
    'quantity', v_qty,
    'unit', prod.price_cents,
    'subtotal', v_sub,
    'discount', v_disc,
    'code', v_code,
    'balance', v_bal,
    'credit', v_credit,
    'total', v_sub - v_disc - v_credit
  );
end;
$$;

create or replace function public.quote_order_qty(p_product uuid, p_code text, p_use_credit boolean, p_qty int)
returns jsonb language plpgsql stable security definer set search_path = public as $$
begin
  if auth.uid() is null then
    return jsonb_build_object('error', 'Log in to check out.');
  end if;
  return public.internal_quote_qty(auth.uid(), p_product, p_code, p_use_credit, p_qty);
end;
$$;

create or replace function public.place_order_qty(
  p_user uuid, p_product uuid, p_server text, p_code text, p_use_credit boolean, p_currency text, p_qty int
) returns jsonb language plpgsql security definer set search_path = public as $$
declare
  q jsonb;
  v_id uuid;
  v_tracked boolean;
  v_held int;
begin
  perform 1 from public.profiles where id = p_user for update;
  if not found then
    return jsonb_build_object('error', 'Your account wasn''t found. Log out and back in.');
  end if;

  q := public.internal_quote_qty(p_user, p_product, p_code, p_use_credit, p_qty);
  if q ? 'error' then
    return q;
  end if;
  v_tracked := (q->>'stock_tracked')::boolean;

  if not v_tracked and char_length(coalesce(trim(p_server), '')) not between 1 and 200 then
    return jsonb_build_object('error', 'Enter your server link.');
  end if;

  insert into public.orders (
    user_id, product_id, product_name, category, server_link, quantity, subtotal_cents, discount_code,
    discount_cents, credit_used_cents, total_cents, pay_currency
  ) values (
    p_user, p_product, q->>'product_name', q->>'category', left(coalesce(trim(p_server), ''), 200), (q->>'quantity')::int,
    (q->>'subtotal')::int, q->>'code', (q->>'discount')::int, (q->>'credit')::int, (q->>'total')::int,
    case when (q->>'total')::int = 0 then 'credit' else p_currency end
  ) returning id into v_id;

  if v_tracked then
    v_held := public.internal_reserve_codes(v_id, p_product, (q->>'quantity')::int);
    if v_held < (q->>'quantity')::int then
      raise exception 'OUT_OF_STOCK';
    end if;
  end if;

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

create or replace function public.internal_mark_paid(p_order uuid, p_invoice text default null)
returns void language plpgsql security definer set search_path = public as $$
declare
  o public.orders%rowtype;
  v_before bigint;
  v_after bigint;
  v_reward bigint;
  v_tracked boolean;
  v_held int;
begin
  select * into o from public.orders where id = p_order for update;
  if not found or o.status in ('paid', 'completed', 'refunded') then
    return;
  end if;
  select stock_tracked into v_tracked from public.products where id = o.product_id;

  -- Paid after being cancelled: take the credit and code use again, and try to hold codes again.
  if o.status = 'cancelled' then
    if o.credit_used_cents > 0 then
      insert into public.credit_ledger (user_id, amount_cents, reason, order_id, note)
      values (o.user_id, -o.credit_used_cents, 'order', o.id, 'Credit used again: order was paid after it was cancelled');
    end if;
    if o.discount_code is not null then
      update public.discount_codes set uses = uses + 1 where code = o.discount_code;
    end if;
    if coalesce(v_tracked, false) then
      select count(*) into v_held from public.product_codes where order_id = o.id and status = 'reserved';
      if v_held < o.quantity then
        perform public.internal_reserve_codes(o.id, o.product_id, o.quantity - v_held);
      end if;
    end if;
  end if;

  update public.orders
     set status = 'paid', paid_at = now(), invoice_id = coalesce(p_invoice, invoice_id)
   where id = o.id;

  -- Hand over the codes. Stock orders are done once delivered.
  if coalesce(v_tracked, false) then
    update public.product_codes set status = 'delivered', delivered_at = now()
     where order_id = o.id and status = 'reserved';
    get diagnostics v_held = row_count;
    if v_held >= o.quantity then
      update public.orders set status = 'completed' where id = o.id;
    else
      update public.orders set payment_note = 'Paid, but only ' || v_held || ' of ' || o.quantity || ' codes were in stock. Add codes and send the rest.'
       where id = o.id;
    end if;
  end if;

  select spent_cents into v_before from public.profiles where id = o.user_id for update;
  v_after := v_before + o.total_cents;
  update public.profiles set spent_cents = v_after where id = o.user_id;

  -- Rewards: $2 credit for every $20 spent.
  v_reward := (v_after / 2000 - v_before / 2000) * 200;
  if v_reward > 0 then
    insert into public.credit_ledger (user_id, amount_cents, reason, order_id, note)
    values (o.user_id, v_reward, 'reward', o.id, 'Rewards: $2 for every $20 spent');
  end if;

  update public.products set sold = sold + o.quantity where id = o.product_id;
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
  update public.product_codes set status = 'available', order_id = null where order_id = o.id and status = 'reserved';
  if o.credit_used_cents > 0 then
    insert into public.credit_ledger (user_id, amount_cents, reason, order_id, note)
    values (o.user_id, o.credit_used_cents, 'order_cancelled', o.id, 'Credit returned: order cancelled');
  end if;
  if o.discount_code is not null then
    update public.discount_codes set uses = greatest(0, uses - 1) where code = o.discount_code;
  end if;
end;
$$;

-- Admin: give an order its missing codes from new stock.
create or replace function public.admin_deliver_missing_codes(p_order uuid)
returns int language plpgsql security definer set search_path = public as $$
declare
  o public.orders%rowtype;
  v_have int;
  v_new int;
begin
  if not public.is_admin() then raise exception 'Admins only'; end if;
  select * into o from public.orders where id = p_order for update;
  if not found or o.status not in ('paid', 'completed') then raise exception 'Only paid orders'; end if;
  select count(*) into v_have from public.product_codes where order_id = o.id and status = 'delivered';
  if v_have >= o.quantity then return 0; end if;
  v_new := public.internal_reserve_codes(o.id, o.product_id, o.quantity - v_have);
  update public.product_codes set status = 'delivered', delivered_at = now() where order_id = o.id and status = 'reserved';
  if v_have + v_new >= o.quantity then
    update public.orders set status = 'completed', payment_note = null where id = o.id;
  end if;
  return v_new;
end;
$$;

-- Permissions
revoke execute on function public.internal_quote_qty(uuid, uuid, text, boolean, int) from public, anon, authenticated;
revoke execute on function public.place_order_qty(uuid, uuid, text, text, boolean, text, int) from public, anon, authenticated;
revoke execute on function public.internal_reserve_codes(uuid, uuid, int) from public, anon, authenticated;
revoke execute on function public.internal_mark_paid(uuid, text) from public, anon, authenticated;
revoke execute on function public.internal_cancel_order(uuid) from public, anon, authenticated;
revoke execute on function public.quote_order_qty(uuid, text, boolean, int) from public, anon;
revoke execute on function public.admin_deliver_missing_codes(uuid) from public, anon;
grant execute on function public.quote_order_qty(uuid, text, boolean, int) to authenticated;
grant execute on function public.stock_counts() to anon, authenticated;
grant execute on function public.admin_deliver_missing_codes(uuid) to authenticated;
grant execute on function public.place_order_qty(uuid, uuid, text, text, boolean, text, int) to service_role;
grant execute on function public.internal_mark_paid(uuid, text) to service_role;
grant execute on function public.internal_cancel_order(uuid) to service_role;
