import { createBrowserClient } from "@supabase/ssr";

/**
 * Part F §F.5/§F.6's Supabase Auth (Slice 5, §F.8) -- the BROWSER client,
 * used only by the sign-in form (a Client Component: `supabase.auth.
 * signInWithOtp` for the magic-link flow, decided directly with Satya).
 * `NEXT_PUBLIC_*` env vars are safe to ship to the client by design: the
 * anon/publishable key is meant to be public (RLS is what actually
 * protects data, per Part D Decision 9) -- this is a DIFFERENT key from
 * `compute/.env`'s `SUPABASE_SERVICE_ROLE_KEY`, which must never reach a
 * browser and never appears here.
 */
export function createClient() {
  return createBrowserClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
  );
}
