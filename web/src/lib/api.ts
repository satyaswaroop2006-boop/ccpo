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

// Mirrors app/schemas.py::SpendItemIn.
export interface SpendItemIn {
  category: string;
  annual_amount: string; // Decimal, sent as a string -- FastAPI/Pydantic parses numeric strings exactly, no float round-trip
  channel?: string;
  geography?: "domestic" | "international";
  merchant_group?: string;
}

// Mirrors app/schemas.py::AssumptionsIn -- only the fields Slice 3 needs
// to set (benefit_need/benefit_unit_value, to satisfy countable benefits
// per evaluateCard's own doc comment below); every other field keeps its
// own server-side default.
export interface AssumptionsIn {
  benefit_need?: Record<string, string>;
  benefit_unit_value?: Record<string, string>;
}

// Mirrors app/schemas.py::TraceLineOut / NACVOut / EvaluateResponse.
export interface TraceLineEntry {
  kind: string;
  amount: string;
  label: string;
  flags: string[];
}

export interface NACVOut {
  steady_state: string;
  year_1: string;
  three_year: string;
  trace: TraceLineEntry[];
}

export interface EvaluateResponse {
  card_key: string;
  gross_reward_value: string;
  milestone_value: string;
  milestone_value_year1: string;
  benefit_value: string;
  waiver_achieved: boolean;
  fee_steady: string;
  fee_year1: string;
  nacv: NACVOut;
  flags: string[];
}

export class EvaluateError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
  }
}

// Mirrors app/schemas.py::ExcludedCardOut / FrontierPointOut /
// RecommendationStepOut / CardClassificationOut / RobustnessOut /
// OptimiseResponse.
export interface ExcludedCardEntry {
  card_key: string;
  reason: string;
}

export interface FrontierPointEntry {
  size: number;
  subset_key: string;
  card_keys: string[];
  pv_exact: string;
}

export interface RecommendationStepEntry {
  size: number;
  delta_v: string;
  t1_pass: boolean;
  t1_threshold: string;
  delta_fee: string;
  delta_gross_benefit: string;
  fee_cover_ratio: string | null;
  t2_pass: boolean;
  low_spend_delta_v: string | null;
  t3_pass: boolean | null;
  passes: boolean;
  explanation: string; // already plain-rupees prose (Part E SS E.9) -- render verbatim, don't re-summarize
}

export interface CardClassificationEntry {
  card_key: string;
  label: string; // "KEEP" | "OPTIONAL" | "CLOSE" | "HOLD" | "ADD" | "DOWNGRADE"
  icv: string;
  overlap: string | null;
  note: string | null;
  downgrade_to: string | null;
}

export interface RobustnessEntry {
  v_expected: string;
  v_low: string;
  v_high: string;
  robustness: string | null; // v_low / v_expected -- null when v_expected <= 0 (no positive value to keep)
  rank_stable: boolean;
}

export interface OptimiseResponse {
  candidates: string[];
  excluded_cards: ExcludedCardEntry[];
  frontier: FrontierPointEntry[];
  recommendation_steps: RecommendationStepEntry[];
  recommended_size: number;
  capped_by_tolerance: boolean;
  recommended_subset_key: string;
  recommended_card_keys: string[];
  recommended_pv_exact: string;
  classification_owned: CardClassificationEntry[];
  classification_candidates: CardClassificationEntry[];
  robustness: RobustnessEntry | null;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    // Never cached (each call is a distinct, unrepeatable computation
    // over this specific input) -- Part F §F.0's "no screen computes a
    // rupee value" boundary: every POST here is the ONE place a screen
    // ever asks compute/ for a number, never derives one itself.
    cache: "no-store",
  });
  if (!res.ok) {
    const text = await res.text();
    let detail: unknown = text;
    try {
      detail = JSON.parse(text).detail ?? text;
    } catch {
      // body wasn't JSON -- fall through with the raw text
    }
    throw new EvaluateError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json();
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

export async function evaluateCard(
  cardKey: string,
  spend: SpendItemIn[],
  assumptions: AssumptionsIn = {},
): Promise<EvaluateResponse> {
  return postJson<EvaluateResponse>("/evaluate", { card_key: cardKey, spend, assumptions });
}

// Part F §F.2.2's Calculator, portfolio mode (Slice 4, §F.8). Deliberately
// minimal request surface -- OptimiseRequest has ~13 fields total
// (candidate_universe, cardinality_mode, icv_meaningful, the champion-
// selection tuning knobs, ...), every one of which already has a sensible
// server-side default (app/schemas.py::OptimiseRequest); Slice 4 exposes
// none of them as UI controls, same "smallest possible" posture as every
// prior slice. `candidate_universe` left unset -> the full live catalog,
// same as the CLI/API's own default. `assumptions` IS threaded through
// (unlike the rest of OptimiseRequest) because it's the same countable-
// benefit necessity `runEvaluate` already has to handle -- see actions.ts.
export async function optimiseCards(spend: SpendItemIn[], assumptions: AssumptionsIn = {}): Promise<OptimiseResponse> {
  return postJson<OptimiseResponse>("/optimise", { spend, assumptions });
}

// Part F §F.2.3's Ingestion Review screen (Slice 6). Mirrors
// app/schemas.py::ReviewQueueItemOut -- `entity_fields` stays an open
// index signature for the same reason `RuleEntry` above does: the
// drafted row's own columns vary per entity_type, and hand-typing one
// interface per type would duplicate Part C/D's vocabulary a second
// time (§F.2.1/#163's own precedent).
export interface ReviewQueueItemEntry {
  source_link_id: string;
  entity_type: string;
  entity_key: string;
  entity_fields: Record<string, unknown>;
  confidence: "high" | "medium" | "low";
  source_url: string;
  source_type: string;
  source_title: string | null;
  source_snapshot_url: string | null;
}

export interface ReviewQueueGroupEntry {
  label: string;
  card_version_id: string | null;
  items: ReviewQueueItemEntry[];
}

// Part F §F.2.3's card-level "ready to publish" indicator (Slice 7).
// Mirrors app/schemas.py::DraftCardStatusOut -- listed separately from
// `groups` above because a card_version with everything already
// approved has nothing left to review (its group disappears entirely,
// Slice 6's own behavior), but the Publish button still needs to
// appear for it.
export interface DraftCardStatusEntry {
  card_key: string;
  card_version_id: string;
  ready_to_publish: boolean;
  problems: string[];
}

export interface ReviewQueueResponse {
  groups: ReviewQueueGroupEntry[];
  draft_cards: DraftCardStatusEntry[];
}

// Thrown when compute/ itself rejects the caller (401/403) -- distinct
// from EvaluateError so admin/page.tsx can tell "not authorized" apart
// from any other API failure, even though both cases currently render
// the same way (redirect already happened in lib/auth.ts's own check
// before this would ever fire in practice; this is the defense-in-depth
// path if the two checks ever disagree).
export class AdminAuthError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
  }
}

async function authedFetch(path: string, accessToken: string, init: RequestInit = {}): Promise<Response> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: { ...(init.headers ?? {}), Authorization: `Bearer ${accessToken}` },
    // Mutation-adjacent, always-fresh data -- never cached, same
    // reasoning as postJson's own no-store (each call reflects the
    // CURRENT reviewer_status, not a stale snapshot).
    cache: "no-store",
  });
  if (res.status === 401 || res.status === 403) {
    throw new AdminAuthError(res.status, await res.text());
  }
  return res;
}

export async function getReviewQueue(accessToken: string): Promise<ReviewQueueResponse> {
  const res = await authedFetch("/review-queue", accessToken);
  if (!res.ok) {
    throw new Error(`GET /review-queue failed: ${res.status} ${await res.text()}`);
  }
  return res.json();
}

export async function approveSourceLink(sourceLinkId: string, accessToken: string): Promise<void> {
  const res = await authedFetch(`/source-links/${encodeURIComponent(sourceLinkId)}/approve`, accessToken, {
    method: "POST",
  });
  if (!res.ok) {
    throw new Error(`POST /source-links/${sourceLinkId}/approve failed: ${res.status} ${await res.text()}`);
  }
}

export async function rejectSourceLink(sourceLinkId: string, note: string, accessToken: string): Promise<void> {
  const res = await authedFetch(`/source-links/${encodeURIComponent(sourceLinkId)}/reject`, accessToken, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ note }),
  });
  if (!res.ok) {
    throw new Error(`POST /source-links/${sourceLinkId}/reject failed: ${res.status} ${await res.text()}`);
  }
}

// Part F §F.2.3/§F.4's guarded Publish button (Slice 7). Mirrors
// app/schemas.py::ScenarioResultOut/PublishCardVersionResponse.
export interface ScenarioResultEntry {
  golden_path: string;
  scenario_name: string;
  passed: boolean;
  diffs: string[];
}

export interface PublishCardVersionResponse {
  card_key: string;
  card_version_id: string;
  scenario_results: ScenarioResultEntry[];
  superseded_version_id: string | null;
}

export async function publishCardVersion(
  cardVersionId: string,
  confirmCardKey: string,
  accessToken: string,
): Promise<PublishCardVersionResponse> {
  const res = await authedFetch(`/card-versions/${encodeURIComponent(cardVersionId)}/publish`, accessToken, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirm_card_key: confirmCardKey }),
  });
  if (!res.ok) {
    // No special-casing of a failed gate check here -- same "clean, honest
    // message, zero special-casing" posture the multi-route-currency error
    // already established (Slice 3, docs/DECISIONS.md #165). In practice
    // the Publish button (page.tsx) only ever renders once `draft_cards`
    // already reports `ready_to_publish: true`, so reaching a gate failure
    // here means state changed between page load and click -- rare enough
    // that Next.js's own error boundary is an honest way to surface it.
    throw new Error(`POST /card-versions/${cardVersionId}/publish failed: ${res.status} ${await res.text()}`);
  }
  return res.json();
}
