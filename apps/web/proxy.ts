import { createServerClient } from "@supabase/ssr";
import { type NextRequest, NextResponse } from "next/server";

import { getMarkdownRewritePath } from "@/lib/markdown/representation";

export async function proxy(request: NextRequest) {
  // Markdown representations are public, so they skip the session refresh.
  const rewritePath = getMarkdownRewritePath(request);
  if (rewritePath) {
    return NextResponse.rewrite(new URL(`${rewritePath}${request.nextUrl.search}`, request.url));
  }

  // Keeps the Supabase session cookie fresh on every request.
  let response = NextResponse.next({ request });
  if (!process.env.NEXT_PUBLIC_SUPABASE_URL) return response;
  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll: () => request.cookies.getAll(),
        setAll: (items) => {
          for (const { name, value } of items) request.cookies.set(name, value);
          response = NextResponse.next({ request });
          for (const { name, value, options } of items) response.cookies.set(name, value, options);
        },
      },
    },
  );
  await supabase.auth.getUser();
  return response;
}

export const config = {
  matcher: [
    // Only named static and metadata files skip the proxy: `.md` mirrors must still match.
    "/((?!_next/static|_next/image|_next/data|_vercel|api/stripe/webhook|apple-icon\\.png$|favicon\\.ico$|icon\\.svg$|icons/|llms\\.txt$|manifest\\.webmanifest$|og-default\\.png$|robots\\.txt$|sitemap\\.xml$).*)",
  ],
};
