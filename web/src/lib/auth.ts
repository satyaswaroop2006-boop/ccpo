import "server-only";
import { createClient } from "@/lib/supabase/server";

/**
 * Part F §F.6's own decision, made concrete: real Supabase Auth is wired
 * up, but access is restricted to a single allow-listed account (Satya's),
 * not a general sign-up flow. The allow-list itself is the one part of
 * this that's meant to be temporary -- `ADMIN_ALLOWED_EMAIL` is a small
 * server-side config value, not hardcoded into this function's own logic,
 * so replacing it with a real roles/permissions check later (when a
 * second reviewer exists) only touches this one comparison, not the auth
 * mechanism itself (session handling, middleware, the client/server
 * Supabase clients) -- exactly the "real plumbing, not a shortcut" F.6
 * called for.
 *
 * `import "server-only"` makes this file (and anything importing it)
 * fail to BUILD if a Client Component ever tries to import it -- the
 * allow-listed email is a server-side value, never meant to ship to the
 * browser bundle at all, not just "not displayed."
 */

export interface AdminSession {
  email: string;
}

/**
 * Returns the admin session if the caller is signed in AND their email
 * matches the allow-list, `null` otherwise (signed out, or signed in as
 * someone else -- both cases handled identically: no access, no
 * distinction leaked to the caller about WHICH reason). Uses `getClaims()`
 * (validates the JWT signature), not `getSession()` -- Supabase's own
 * current guidance for a server-side authorization check.
 */
export async function getAdminSession(): Promise<AdminSession | null> {
  const allowedEmail = process.env.ADMIN_ALLOWED_EMAIL;
  if (!allowedEmail) {
    // Misconfiguration, not "nobody's allowed in" -- fail loudly in
    // server logs rather than silently locking out (or, worse, silently
    // admitting everyone if a future edit ever loosened the comparison
    // below). Matches this repo's own "raise loudly on a misconfigured
    // DATABASE_URL rather than silently falling back" precedent
    // (app/main.py::get_repository).
    throw new Error("ADMIN_ALLOWED_EMAIL is not configured -- refusing to evaluate admin access.");
  }

  const supabase = await createClient();
  const { data, error } = await supabase.auth.getClaims();
  if (error || !data?.claims) return null;

  const email = data.claims.email as string | undefined;
  if (!email || email.toLowerCase() !== allowedEmail.toLowerCase()) return null;

  return { email };
}
