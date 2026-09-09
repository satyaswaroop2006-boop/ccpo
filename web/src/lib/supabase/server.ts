import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";

/**
 * Part F §F.5/§F.6's Supabase Auth (Slice 5, §F.8) -- the SERVER client,
 * for Server Components/Actions/Route Handlers to read the session
 * `middleware.ts` already refreshed. `cookieStore.set` inside a Server
 * Component throws (Server Components can't write cookies) -- swallowed
 * here per Supabase's own documented pattern, since `middleware.ts`
 * already handles the actual refresh/write; a Server Component only ever
 * needs to READ the session this way.
 */
export async function createClient() {
  const cookieStore = await cookies();

  return createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return cookieStore.getAll();
        },
        setAll(cookiesToSet) {
          try {
            cookiesToSet.forEach(({ name, value, options }) => cookieStore.set(name, value, options));
          } catch {
            // Server Components cannot write cookies -- middleware.ts already refreshes the session.
          }
        },
      },
    },
  );
}
