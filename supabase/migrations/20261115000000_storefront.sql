-- Giggle & Snooze storefront: catalog, entitlements, memberships (ADR-029-storefront-stack).
-- Writers of entitlements and memberships are Stripe webhooks using the service role only.

create type book_access as enum ('free', 'members', 'paid');
create type entitlement_source as enum ('purchase', 'membership', 'gift');

create table profiles (
  id uuid primary key references auth.users on delete cascade,
  display_name text,
  stripe_customer_id text unique,
  created_at timestamptz not null default now()
);

create table books (
  id uuid primary key default gen_random_uuid(),
  slug text not null unique,
  title text not null,
  subtitle text,
  blurb text,
  access book_access not null default 'paid',
  member_included boolean not null default true,  -- paid books also readable by active members
  price_cents integer check (price_cents is null or price_cents >= 0),
  currency text not null default 'usd',
  stripe_price_id text,
  page_count integer,
  cover_path text,          -- storage: covers/<slug>/cover.jpg (public bucket)
  epub_path text,           -- storage: epubs/<slug>/<slug>.epub (private bucket)
  sample_epub_path text,    -- optional free sample for paid books
  published boolean not null default false,
  published_at timestamptz,
  created_at timestamptz not null default now(),
  constraint paid_has_price check (access <> 'paid' or price_cents is not null)
);

create table entitlements (
  user_id uuid not null references auth.users on delete cascade,
  book_id uuid not null references books on delete cascade,
  source entitlement_source not null,
  stripe_payment_intent text,
  created_at timestamptz not null default now(),
  primary key (user_id, book_id)
);

create table memberships (
  user_id uuid primary key references auth.users on delete cascade,
  stripe_subscription_id text unique,
  status text not null,                 -- Stripe status: active, trialing, past_due, canceled...
  current_period_end timestamptz,
  updated_at timestamptz not null default now()
);

-- True when the user may read the full book. Used by RLS and by the signed-URL endpoint.
create function can_read_book(p_user uuid, p_book uuid) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (
    select 1 from books b
    where b.id = p_book and b.published
      and (
        (b.access = 'free' and p_user is not null)
        or (b.access = 'members' and exists (
              select 1 from memberships m
              where m.user_id = p_user and m.status in ('active', 'trialing')
                and (m.current_period_end is null or m.current_period_end > now())))
        or (b.access = 'paid' and (
              exists (select 1 from entitlements e where e.user_id = p_user and e.book_id = b.id)
              or (b.member_included and exists (
                    select 1 from memberships m
                    where m.user_id = p_user and m.status in ('active', 'trialing')
                      and (m.current_period_end is null or m.current_period_end > now())))))
      )
  );
$$;

alter table profiles enable row level security;
alter table books enable row level security;
alter table entitlements enable row level security;
alter table memberships enable row level security;

create policy "profile: own row" on profiles for select using (id = auth.uid());
create policy "profile: update own" on profiles for update using (id = auth.uid());
create policy "books: published are public" on books for select using (published);
create policy "entitlements: own rows" on entitlements for select using (user_id = auth.uid());
create policy "memberships: own row" on memberships for select using (user_id = auth.uid());
-- No insert/update/delete policies on entitlements, memberships or books: service role only.

-- Create a profile row for every new auth user.
create function handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into profiles (id) values (new.id);
  return new;
end $$;
create trigger on_auth_user_created after insert on auth.users
  for each row execute function handle_new_user();

-- Storage: covers public, epubs private (reads only through signed URLs minted by the server).
insert into storage.buckets (id, name, public) values
  ('covers', 'covers', true),
  ('epubs', 'epubs', false)
on conflict (id) do nothing;

create policy "covers: public read" on storage.objects for select using (bucket_id = 'covers');
-- No policies on bucket epubs: only the service role can read or write it.

-- can_read_book takes any user id, so only the server (service role) may call it.
revoke execute on function can_read_book(uuid, uuid) from public, anon, authenticated;
