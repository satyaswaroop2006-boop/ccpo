import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

/**
 * Part F §F.5/§F.6's Supabase Auth (Slice 5, §F.8). Refreshes the session
 * once per navigation, before any page/Server Action runs -- the documented
 * pattern to avoid the "two simultaneous requests both try to refresh the
 * same expired token" race @supabase/ssr's own README warns about.
 *
 * This middleware does NOT gate access by itself (no redirect-to-login
 * here) -- Slice 5's own placeholder page (`/admin`) does that check
 * itself via `lib/auth.ts::requireAdmin`, matching Part F §F.8's own
 * instruction to prove the mechanism on a placeholder BEFORE any real
 * mutation sits behind it. Centralizing the redirect in middleware is a
 * reasonable later refactor once there's more than one protected route
 * (Slice 6's Review UI), not done here to keep this slice's blast radius
 * to exactly what it claims: session refresh, nothing else.
 */
export async function middleware(request: NextRequest) {
  const response = NextResponse.next({ request: { headers: request.headers } });

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return request.cookies.getAll();
        },
        setAll(cookiesToSet) {
          cookiesToSet.forEach(({ name, value, options }) => response.cookies.set(name, value, options));
        },
      },
    },
  );

  // Validates the JWT signature and triggers a refresh if the session is
  // expired -- getClaims(), not getSession(), per Supabase's own current
  // guidance ("use getClaims to protect pages/sessions server-side").
  await supabase.auth.getClaims();

  return response;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\.svg$).*)"],
};
