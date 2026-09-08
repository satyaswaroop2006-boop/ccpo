/**
 * Typed client for the compute/ FastAPI service (Part F §F.3). Hand-written
 * types mirroring app/schemas.py's own Pydantic models -- cheap to keep in
 * sync at this size (§F.5's own "hand-written or generated, cheap either
 * way" call); generating from the OpenAPI schema is a reasonable follow-up
 * once the surface grows past what's comfortable to eyeball against the
 * Python source.
 *
 * Server-only: every function here is called from a Server Component or
 * route handler (never a Client Component), so `API_BASE_URL` never needs
 * the `NEXT_PUBLIC_` prefix and never reaches the browser bundle -- Part
 * F §F.0's "no screen computes a rupee value" extends naturally to "no
 * screen even NEEDS direct network access to compute/", since every
 * number already arrives pre-rendered from the server fetch below.
 */

const API_BASE_URL = process.env.API_BASE_URL ?? "http://localhost:8000";

// Mirrors app/schemas.py::CardSummaryOut field-for-field.
export interface CardSummary {
  card_key: string;
  name: string;
  issuer_name: string;
  network: string;
  tier: string | null;
  segment: string | null;
  joining_fee: string; // Decimal, serialized as a string (exact, no float rounding)
  annual_fee: string;
  currency_key: string;
}

// Each rule-breakdown entry mirrors an engine dataclass serialized by
// app/schemas.py::_jsonable -- deliberately loose here too, for the same
// reason it's generic on the Python side (§F.2.1/#163): a hand-typed
// interface per dataclass would be the SAME drift risk moved one layer,
// not removed. Known common fields are still named for editor ergonomics;
// anything else still comes through via the index signature.
export interface RuleEntry {
  key: string;
  [field: string]: unknown;
}

// Mirrors app/schemas.py::CardDetailOut.
export interface CardDetail extends CardSummary {
  earning_rules: RuleEntry[];
  caps: RuleEntry[];
  thresholds: RuleEntry[];
  exclusions: RuleEntry[];
  benefits: RuleEntry[];
  surcharges: RuleEntry[];
}

export class CardNotFoundError extends Error {
  constructor(public readonly cardKey: string) {
    super(`unknown card_key ${JSON.stringify(cardKey)}`);
  }
}

async function apiFetch(path: string): Promise<Response> {
  return fetch(`${API_BASE_URL}${path}`, {
    // Server-rendered catalog data (Part F §F.5's own SEO rationale for
    // Next.js) -- revalidate periodically rather than caching forever or
    // re-fetching on every request; the catalog changes only on a publish,
    // not per-request.
    next: { revalidate: 60 },
  });
}

export async function listCards(): Promise<CardSummary[]> {
  const res = await apiFetch("/cards");
  if (!res.ok) {
    throw new Error(`GET /cards failed: ${res.status} ${await res.text()}`);
  }
  return res.json();
}

export async function getCard(cardKey: string): Promise<CardDetail> {
  const res = await apiFetch(`/cards/${encodeURIComponent(cardKey)}`);
  if (res.status === 404) {
    throw new CardNotFoundError(cardKey);
  }
  if (!res.ok) {
    throw new Error(`GET /cards/${cardKey} failed: ${res.status} ${await res.text()}`);
  }
  return res.json();
}
