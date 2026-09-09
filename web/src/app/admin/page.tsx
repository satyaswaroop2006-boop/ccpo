import { redirect } from "next/navigation";
import { getAdminSession } from "@/lib/auth";
import { signOut } from "./actions";

// Part F §F.8's own Slice 5 instruction: "applied to a placeholder
// endpoint first to prove the mechanism works before any real mutation
// sits behind it." This page does nothing else -- it's the proof, not a
// real feature. Slice 6 replaces/extends this with the actual Ingestion
// Review screen, behind the same `getAdminSession` gate.
//
// force-dynamic is not just this repo's usual build-robustness fix
// (Catalog/Calculator, #164/#165) -- it's a correctness requirement
// here: an auth-gated page depends on the VISITOR's own session cookie,
// so it can never be valid to statically prerender it once at build
// time. Found the same way as before (a build with no ADMIN_ALLOWED_
// EMAIL set threw during static prerendering), but the fix matters for
// a stronger reason this time.
export const dynamic = "force-dynamic";

export const metadata = { title: "Admin | CCPO" };

export default async function AdminPage() {
  const session = await getAdminSession();
  if (!session) {
    redirect("/login");
  }

  return (
    <main className="mx-auto w-full max-w-lg flex-1 px-6 py-16">
      <h1 className="text-2xl font-semibold tracking-tight text-neutral-900">Admin</h1>
      <p className="mt-2 text-sm text-neutral-600">
        Signed in as <span className="font-medium text-neutral-900">{session.email}</span>. Auth is
        wired up (Part F Slice 5) -- the Ingestion Review screen (Slice 6) lands here next.
      </p>
      <form action={signOut} className="mt-6">
        <button
          type="submit"
          className="rounded-md border border-neutral-300 px-4 py-2 text-sm font-medium text-neutral-700 hover:border-neutral-400"
        >
          Sign out
        </button>
      </form>
    </main>
  );
}
