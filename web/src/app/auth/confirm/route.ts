import { type EmailOtpType } from "@supabase/supabase-js";
import { redirect } from "next/navigation";
import { type NextRequest } from "next/server";
import { createClient } from "@/lib/supabase/server";

/**
 * Part F §F.5/§F.6's magic-link callback (Slice 5, §F.8).
 *
 * Handles `?code=` via `exchangeCodeForSession` -- confirmed directly
 * against the installed package source (`node_modules/@supabase/ssr/
 * dist/module/createBrowserClient.js` and `createServerClient.js`), NOT
 * assumed: both of `@supabase/ssr`'s client factories default to
 * `flowType: "pkce"`. Under PKCE, Supabase's own hosted `/auth/v1/verify`
 * endpoint -- which the DEFAULT (unmodified) Magic Link email template's
 * `{{ .ConfirmationURL }}` points to -- redirects back here with `?code=`,
 * not `token_hash`/`type`. A real dashboard limitation surfaced this: the
 * email template couldn't be edited to send `token_hash` (the alternative
 * PKCE pattern Supabase's own docs also describe), so this route was
 * rewritten to match what the UNMODIFIED default template actually
 * produces, rather than requiring a template change that turned out not
 * to be available. `token_hash`/`type` (`verifyOtp`) is kept as a second
 * path -- genuinely reachable if the template is ever customized later
 * (Supabase's own docs describe both), not dead code kept out of caution.
 */
export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const next = searchParams.get("next") ?? "/admin";
  const supabase = await createClient();

  const code = searchParams.get("code");
  if (code) {
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (!error) {
      redirect(next);
    }
  }

  const token_hash = searchParams.get("token_hash");
  const type = searchParams.get("type") as EmailOtpType | null;
  if (token_hash && type) {
    const { error } = await supabase.auth.verifyOtp({ type, token_hash });
    if (!error) {
      redirect(next);
    }
  }

  redirect("/login?error=link_invalid");
}
