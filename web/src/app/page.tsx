import Link from "next/link";
import { listCards } from "@/lib/api";
import { formatRupees, titleCase } from "@/lib/format";

// Part F §F.2.1's Catalog screen (Slice 2, §F.8). Server Component: the
// fetch in listCards() runs on the server, so this page ships pre-rendered
// HTML -- no client-side loading state needed for the common case, and
// real SEO for the public catalog (§F.5's own reason for choosing Next.js
// over a plain SPA). No auth, no forms, no mutations -- just a rendering
// of what GET /cards already returns.
export const metadata = {
  title: "Card Catalog | CCPO",
  description: "Browse every published credit card in the CCPO catalog.",
};

// Without this, Next.js tries to STATICALLY prerender this route at
// `next build` time (no dynamic segments on "/"), which means the build
// itself depends on compute/'s API being reachable -- a real failure
// found by running `npm run build` with the API stopped: a hard build
// error, not a graceful fallback. `force-dynamic` renders per-request
// instead (still deployment-order-independent), while listCards()'s own
// `next: { revalidate: 60 }` fetch option keeps the underlying data
// cached and fresh -- so this trades build-time static HTML for build
// robustness, not for the caching behavior itself.
export const dynamic = "force-dynamic";

export default async function CatalogPage() {
  const cards = await listCards();

  return (
    <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-12">
      <header className="mb-10">
        <h1 className="text-3xl font-semibold tracking-tight text-neutral-900">
          Card Catalog
        </h1>
        <p className="mt-2 text-neutral-600">
          {cards.length} published card{cards.length === 1 ? "" : "s"}.
        </p>
      </header>

      <ul className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {cards.map((card) => (
          <li key={card.card_key}>
            <Link
              href={`/cards/${card.card_key}`}
              className="block h-full rounded-lg border border-neutral-200 p-5 transition hover:border-neutral-400 hover:shadow-sm"
            >
              <div className="flex items-start justify-between gap-2">
                <h2 className="text-lg font-medium text-neutral-900">
                  {card.name}
                </h2>
                <span className="shrink-0 rounded-full bg-neutral-100 px-2 py-0.5 text-xs font-medium uppercase text-neutral-600">
                  {card.network}
                </span>
              </div>
              <p className="mt-1 text-sm text-neutral-500">{card.issuer_name}</p>

              <dl className="mt-4 space-y-1 text-sm">
                <div className="flex justify-between">
                  <dt className="text-neutral-500">Annual fee</dt>
                  <dd className="font-medium text-neutral-900">
                    {formatRupees(card.annual_fee)}
                  </dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-neutral-500">Joining fee</dt>
                  <dd className="font-medium text-neutral-900">
                    {formatRupees(card.joining_fee)}
                  </dd>
                </div>
                {card.segment && (
                  <div className="flex justify-between">
                    <dt className="text-neutral-500">Segment</dt>
                    <dd className="text-neutral-700">{titleCase(card.segment)}</dd>
                  </div>
                )}
              </dl>
            </Link>
          </li>
        ))}
      </ul>
    </main>
  );
}
