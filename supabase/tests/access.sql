-- Minimal access test. Run: supabase db reset && psql "$(supabase status -o env | grep DB_URL | cut -d= -f2- | tr -d '\"')" -f supabase/tests/access.sql
begin;
insert into auth.users (id, email) values
  ('00000000-0000-0000-0000-0000000000a1', 'free@example.com'),
  ('00000000-0000-0000-0000-0000000000a2', 'buyer@example.com'),
  ('00000000-0000-0000-0000-0000000000a3', 'member@example.com');
insert into books (id, slug, title, access, price_cents, published) values
  ('10000000-0000-0000-0000-000000000001', 'free-book', 'Free', 'free', null, true),
  ('10000000-0000-0000-0000-000000000002', 'paid-book', 'Paid', 'paid', 599, true),
  ('10000000-0000-0000-0000-000000000003', 'draft-book', 'Draft', 'free', null, false);
insert into entitlements (user_id, book_id, source) values
  ('00000000-0000-0000-0000-0000000000a2', '10000000-0000-0000-0000-000000000002', 'purchase');
insert into memberships (user_id, status, current_period_end) values
  ('00000000-0000-0000-0000-0000000000a3', 'active', now() + interval '30 days');

do $$
declare u_free uuid := '00000000-0000-0000-0000-0000000000a1';
        u_buy  uuid := '00000000-0000-0000-0000-0000000000a2';
        u_mem  uuid := '00000000-0000-0000-0000-0000000000a3';
        b_free uuid := '10000000-0000-0000-0000-000000000001';
        b_paid uuid := '10000000-0000-0000-0000-000000000002';
        b_draft uuid := '10000000-0000-0000-0000-000000000003';
begin
  assert can_read_book(u_free, b_free), 'signed-in user reads free book';
  assert not can_read_book(null, b_free), 'signed-out user cannot read full free book';
  assert not can_read_book(u_free, b_paid), 'free user cannot read paid book';
  assert can_read_book(u_buy, b_paid), 'purchaser reads paid book';
  assert can_read_book(u_mem, b_paid), 'member reads member-included paid book';
  assert not can_read_book(u_mem, b_draft), 'nobody reads unpublished book';
  raise notice 'access tests passed';
end $$;
rollback;
