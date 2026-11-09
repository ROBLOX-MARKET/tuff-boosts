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

-- Add the 15 boost products
insert into public.products (name, category, price_cents, description, image_url) values
  ('2x Server Boosts (1 Month)', 'boosts', 79, '2 server boosts for 1 month, sent to your server.', '/products/boosts-2x.svg'),
  ('4x Server Boosts (1 Month)', 'boosts', 120, '4 server boosts for 1 month, sent to your server.', '/products/boosts-4x.svg'),
  ('6x Server Boosts (1 Month)', 'boosts', 145, '6 server boosts for 1 month, sent to your server.', '/products/boosts-6x.svg'),
  ('8x Server Boosts (1 Month)', 'boosts', 189, '8 server boosts for 1 month, sent to your server.', '/products/boosts-8x.svg'),
  ('10x Server Boosts (1 Month)', 'boosts', 229, '10 server boosts for 1 month, sent to your server.', '/products/boosts-10x.svg'),
  ('12x Server Boosts (1 Month)', 'boosts', 259, '12 server boosts for 1 month, sent to your server.', '/products/boosts-12x.svg'),
  ('14x Server Boosts (1 Month)', 'boosts', 289, '14 server boosts for 1 month, sent to your server.', '/products/boosts-14x.svg'),
  ('16x Server Boosts (1 Month)', 'boosts', 309, '16 server boosts for 1 month, sent to your server.', '/products/boosts-16x.svg'),
  ('18x Server Boosts (1 Month)', 'boosts', 339, '18 server boosts for 1 month, sent to your server.', '/products/boosts-18x.svg'),
  ('20x Server Boosts (1 Month)', 'boosts', 379, '20 server boosts for 1 month, sent to your server.', '/products/boosts-20x.svg'),
  ('22x Server Boosts (1 Month)', 'boosts', 399, '22 server boosts for 1 month, sent to your server.', '/products/boosts-22x.svg'),
  ('24x Server Boosts (1 Month)', 'boosts', 439, '24 server boosts for 1 month, sent to your server.', '/products/boosts-24x.svg'),
  ('26x Server Boosts (1 Month)', 'boosts', 479, '26 server boosts for 1 month, sent to your server.', '/products/boosts-26x.svg'),
  ('28x Server Boosts (1 Month)', 'boosts', 499, '28 server boosts for 1 month, sent to your server.', '/products/boosts-28x.svg'),
  ('30x Server Boosts (1 Month)', 'boosts', 539, '30 server boosts for 1 month, sent to your server.', '/products/boosts-30x.svg');
