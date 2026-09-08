import Link from "next/link";

// Shared top nav, added in Slice 3 now that there's a second real page
// (Catalog + Calculator) to link between. No auth-gated links yet --
// Ingestion Review (Slice 6+) adds one once F.6's auth is wired up.
export function SiteNav() {
  return (
    <header className="border-b border-neutral-200">
      <nav className="mx-auto flex w-full max-w-5xl items-center gap-6 px-6 py-4">
        <Link href="/" className="font-semibold tracking-tight text-neutral-900">
          CCPO
        </Link>
        <Link href="/" className="text-sm text-neutral-600 hover:text-neutral-900">
          Catalog
        </Link>
        <Link href="/calculator" className="text-sm text-neutral-600 hover:text-neutral-900">
          Calculator
        </Link>
      </nav>
    </header>
  );
}
