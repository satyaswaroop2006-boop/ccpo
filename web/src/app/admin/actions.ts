"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { approveSourceLink, publishCardVersion, rejectSourceLink } from "@/lib/api";
import { getAccessToken, getAdminSession } from "@/lib/auth";
import { createClient } from "@/lib/supabase/server";

export async function signOut() {
  const supabase = await createClient();
  await supabase.auth.signOut();
  redirect("/login");
}

/**
 * Part F §F.2.3/§F.4's Approve/Reject actions (Slice 6, docs/DECISIONS.md
 * #168) -- plain `<form action={...}>` bindings, no Client Component or
 * fetch call from the browser, same "no client-side JS needed for the
 * common case" posture Slice 2's Catalog screen already established.
 *
 * Re-checks `getAdminSession()` here even though `/admin` (page.tsx)
 * already gated the page load -- a Server Action is its own request,
 * reachable independently of how the page that rendered its `<form>`
 * got loaded (Next.js does not re-run a page's own auth check before
 * invoking an action bound from it). compute/'s own `app/auth.py::
 * get_admin_session` is the REAL enforcement either way (F.6); this is
 * the same fast, user-facing fail this app's own admin/page.tsx already
 * relies on, not a substitute for it.
 */
async function requireAccessToken(): Promise<string> {
  const session = await getAdminSession();
  if (!session) {
    redirect("/login");
  }
  const token = await getAccessToken();
  if (!token) {
    redirect("/login");
  }
  return token;
}

export async function approveSourceLinkAction(formData: FormData): Promise<void> {
  const token = await requireAccessToken();
  const sourceLinkId = formData.get("source_link_id");
  if (typeof sourceLinkId !== "string" || !sourceLinkId) {
    throw new Error("missing source_link_id");
  }
  await approveSourceLink(sourceLinkId, token);
  revalidatePath("/admin");
}

export async function rejectSourceLinkAction(formData: FormData): Promise<void> {
  const token = await requireAccessToken();
  const sourceLinkId = formData.get("source_link_id");
  const note = formData.get("note");
  if (typeof sourceLinkId !== "string" || !sourceLinkId) {
    throw new Error("missing source_link_id");
  }
  if (typeof note !== "string" || note.trim().length === 0) {
    // Defense in depth -- the form's own `required` attribute (page.tsx)
    // already stops an empty submission client-side; compute/'s
    // `RejectSourceLinkRequest(min_length=1)` is the real enforcement
    // (F.2.3: "Reject (with a required note)").
    throw new Error("a note is required to reject a source link");
  }
  await rejectSourceLink(sourceLinkId, note.trim(), token);
  revalidatePath("/admin");
}

/**
 * Part F §F.2.3/§F.4's guarded Publish button (Slice 7, docs/
 * DECISIONS.md #170) -- the one irreversible action in this UI (Part D
 * Decision 2). `confirm_card_key` comes from the SAME form as
 * `card_version_id` (page.tsx's `<input pattern={cardKey}>` blocks
 * submission client-side until the typed text matches exactly, no JS
 * needed) -- re-verified server-side by compute/'s own endpoint, not
 * trusted from the client alone, same posture as reject's own note.
 */
export async function publishCardVersionAction(formData: FormData): Promise<void> {
  const token = await requireAccessToken();
  const cardVersionId = formData.get("card_version_id");
  const confirmCardKey = formData.get("confirm_card_key");
  if (typeof cardVersionId !== "string" || !cardVersionId) {
    throw new Error("missing card_version_id");
  }
  if (typeof confirmCardKey !== "string" || !confirmCardKey) {
    throw new Error("missing confirm_card_key");
  }
  await publishCardVersion(cardVersionId, confirmCardKey, token);
  revalidatePath("/admin");
}
