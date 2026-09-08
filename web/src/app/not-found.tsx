import Link from "next/link";

// Next.js's own default not-found page ships its own (dark) styling,
// independent of this app's globals.css theme -- visually jarring on an
// otherwise light-themed site. This overrides it for every route (card
// detail's own 404 via notFound() included) so a missing card and a
// missing page both land on the same, on-brand page.
export default function NotFound() {
  return (
    <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col items-center justify-center px-6 py-24 text-center">
      <p className="text-sm font-medium uppercase tracking-wide text-neutral-400">
        404
      </p>
      <h1 className="mt-2 text-2xl font-semibold text-neutral-900">
        We couldn&apos;t find that.
      </h1>
      <p className="mt-2 text-neutral-500">
        The card or page you&apos;re looking for doesn&apos;t exist, or isn&apos;t published.
      </p>
      <Link
        href="/"
        className="mt-6 rounded-md border border-neutral-200 px-4 py-2 text-sm font-medium text-neutral-700 hover:border-neutral-400"
      >
        ← Back to the catalog
      </Link>
    </main>
  );
}
