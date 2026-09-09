import Link from "next/link";

// Shared top nav, added in Slice 3 now that there's a second real page
// (Catalog + Calculator) to link between. The Admin link (Slice 5) is
// safe to show unconditionally -- /admin itself redirects to /login for
// anyone without a valid admin session (getAdminSession), so there's no
// information leak in just showing the link; a "signed in as X"
// indicator here is reasonable polish for Slice 6, once there's an
// actual feature behind the gate rather than a placeholder.
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
        <Link href="/admin" className="ml-auto text-sm text-neutral-600 hover:text-neutral-900">
          Admin
        </Link>
      </nav>
    </header>
  );
}
