-- Sample catalog rows (no files). Safe to run repeatedly.
insert into books (slug, title, subtitle, blurb, access, member_included, price_cents, page_count, published, published_at)
values
  ('sample-free-story', 'The Sleepy Little Cloud', 'A free bedtime story',
   'A small cloud drifts across the evening sky looking for the perfect place to dream.',
   'free', false, null, 12, true, now()),
  ('sample-paid-story', 'The Snail Mail Express', 'A Whisperwood Hollow story',
   'Clara and Barnaby deliver the strangest parcels in the forest, one slow mile at a time.',
   'paid', true, 499, 20, true, now())
on conflict (slug) do nothing;
