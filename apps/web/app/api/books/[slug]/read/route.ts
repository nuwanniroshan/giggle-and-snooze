import { NextResponse } from "next/server";

import { site } from "@/lib/site";
import { createAdminClient } from "@/lib/supabase/admin";
import { createClient } from "@/lib/supabase/server";

const SIGNED_URL_SECONDS = 300;

/** Mints a short-lived signed EPUB URL after the entitlement check, then opens the reader. */
export async function GET(_request: Request, { params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const supabase = await createClient();
  const { data: auth } = await supabase.auth.getUser();
  const admin = createAdminClient();

  const { data: book } = await admin.from("books").select("id, epub_path, sample_epub_path").eq("slug", slug).eq("published", true).maybeSingle();
  if (!book) return new NextResponse("Not found", { status: 404 });

  let path: string | null = null;
  if (auth.user) {
    const { data: ok } = await admin.rpc("can_read_book", { p_user: auth.user.id, p_book: book.id });
    if (ok) path = book.epub_path;
  }
  path ??= book.sample_epub_path;
  if (!path) {
    return NextResponse.redirect(new URL(auth.user ? `/books/${slug}` : "/account", site.url), 303);
  }

  const { data: signed } = await admin.storage.from("epubs").createSignedUrl(path, SIGNED_URL_SECONDS);
  if (!signed) return new NextResponse("Could not open book", { status: 500 });
  return NextResponse.redirect(`${site.readerUrl}/?src=${encodeURIComponent(signed.signedUrl)}`, 303);
}
