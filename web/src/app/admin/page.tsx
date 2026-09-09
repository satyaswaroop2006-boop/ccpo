import { redirect } from "next/navigation";
import { getReviewQueue, type ReviewQueueItemEntry } from "@/lib/api";
import { getAccessToken, getAdminSession } from "@/lib/auth";
import { approveSourceLinkAction, rejectSourceLinkAction, signOut } from "./actions";

// Part F §F.8's own Slice 5 instruction was "a placeholder endpoint first
// to prove the mechanism works before any real mutation sits behind it" --
// this is that real mutation (Slice 6, docs/DECISIONS.md #168): the
// Ingestion Review screen itself, replacing Slice 5's placeholder text
// behind the SAME `getAdminSession` gate. Publish (Slice 7) isn't here
// yet -- it needs a schema prerequisite (`card_versions.bundle_path`,
// flagged in Part F §F.4's own final read-through) this slice doesn't
// touch.
//
// force-dynamic: an auth-gated page depending on the visitor's own
// session cookie (and now also live, frequently-changing review-queue
// data) can never validly be static -- same correctness reasoning as
// Slice 5's own placeholder page, stronger now that the page has real
// data to go stale.
export const dynamic = "force-dynamic";

export const metadata = { title: "Ingestion Review | CCPO" };

function formatFieldValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function ReviewItemRow({ item }: { item: ReviewQueueItemEntry }) {
  return (
    <li className="rounded-md border border-neutral-200 p-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-sm font-medium text-neutral-900">
          {item.entity_type} <code className="rounded bg-neutral-100 px-1 py-0.5 text-xs">{item.entity_key}</code>
        </span>
        <span className="text-xs uppercase tracking-wide text-neutral-500">confidence: {item.confidence}</span>
      </div>

      {Object.keys(item.entity_fields).length > 0 && (
        <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-neutral-700 sm:grid-cols-3">
          {Object.entries(item.entity_fields).map(([key, value]) => (
            <div key={key} className="min-w-0">
              <dt className="text-neutral-400">{key}</dt>
              <dd className="truncate" title={formatFieldValue(value)}>
                {formatFieldValue(value)}
              </dd>
            </div>
          ))}
        </dl>
      )}

      <p className="mt-2 text-xs text-neutral-600">
        Source: {item.source_title ?? item.source_type} ({item.source_type}
        {") "}
        {item.source_snapshot_url ? (
          <a href={item.source_snapshot_url} target="_blank" rel="noreferrer" className="underline">
            view captured document
          </a>
        ) : (
          <span className="italic text-neutral-400">no captured snapshot on file</span>
        )}
      </p>

      <div className="mt-3 flex flex-wrap items-center gap-3">
        <form action={approveSourceLinkAction}>
          <input type="hidden" name="source_link_id" value={item.source_link_id} />
          <button
            type="submit"
            className="rounded-md border border-green-600 px-3 py-1 text-xs font-medium text-green-700 hover:bg-green-50"
          >
            Approve
          </button>
        </form>
        <form action={rejectSourceLinkAction} className="flex min-w-[16rem] flex-1 items-center gap-2">
          <input type="hidden" name="source_link_id" value={item.source_link_id} />
          <input
            type="text"
            name="note"
            required
            placeholder="Reason for rejecting (required)"
            className="flex-1 rounded-md border border-neutral-300 px-2 py-1 text-xs"
          />
          <button
            type="submit"
            className="shrink-0 rounded-md border border-red-600 px-3 py-1 text-xs font-medium text-red-700 hover:bg-red-50"
          >
            Reject
          </button>
        </form>
      </div>
    </li>
  );
}

export default async function AdminPage() {
  const session = await getAdminSession();
  if (!session) {
    redirect("/login");
  }

  // Guaranteed non-null once `getAdminSession()` above succeeded (both
  // read the same underlying session) -- redirecting instead of a
  // non-null assertion, so an inconsistency between the two calls fails
  // safe (back to /login) rather than crashing the page.
  const accessToken = await getAccessToken();
  if (!accessToken) {
    redirect("/login");
  }

  const { groups } = await getReviewQueue(accessToken);

  return (
    <main className="mx-auto w-full max-w-3xl flex-1 px-6 py-16">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold tracking-tight text-neutral-900">Ingestion Review</h1>
        <form action={signOut}>
          <button
            type="submit"
            className="rounded-md border border-neutral-300 px-4 py-2 text-sm font-medium text-neutral-700 hover:border-neutral-400"
          >
            Sign out
          </button>
        </form>
      </div>
      <p className="mt-2 text-sm text-neutral-600">
        Signed in as <span className="font-medium text-neutral-900">{session.email}</span>. Approve or reject each
        cited source below (Part F §F.2.3) -- Publish lands in a later slice.
      </p>

      {groups.length === 0 ? (
        <p className="mt-8 text-sm text-neutral-500">Review queue is empty -- no unreviewed source_links.</p>
      ) : (
        <div className="mt-8 space-y-8">
          {groups.map((group) => (
            <section key={group.label} className="rounded-lg border border-neutral-200 p-4">
              <h2 className="text-sm font-semibold text-neutral-900">{group.label}</h2>
              <ul className="mt-3 space-y-4">
                {group.items.map((item) => (
                  <ReviewItemRow key={item.source_link_id} item={item} />
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
    </main>
  );
}
