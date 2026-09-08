import type { CardClassificationEntry, CardSummary, FrontierPointEntry } from "@/lib/api";
import { formatPercent, formatRupees, titleCase } from "@/lib/format";
import { Stat } from "@/components/Stat";
import type { RunOptimiseResult } from "./actions";

// Part F §F.2.2's Calculator, portfolio mode (Slice 4, §F.8): every
// section here is a direct rendering of one field already in
// OptimiseResponse (per §E.15's own forward pointer -- frontier table,
// checklist, classification set, robustness) -- nothing on this screen
// computes a rupee value or a ranking of its own (§F.0).
export function OptimiseResultPanel({ result, cards }: { result: RunOptimiseResult; cards: CardSummary[] }) {
  if (!result.ok) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 p-5 text-red-800">
        <p className="font-medium">Couldn&apos;t build a recommendation.</p>
        <p className="mt-1 text-sm">{result.error}</p>
      </div>
    );
  }

  const { data } = result;
  const nameByKey = new Map(cards.map((c) => [c.card_key, c.name]));
  const cardName = (key: string) => nameByKey.get(key) ?? key;

  return (
    <div className="space-y-6">
      <div className="rounded-lg border border-neutral-200 p-6">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
          Recommended portfolio
        </h2>
        <p className="mt-2 text-lg font-semibold text-neutral-900">
          {data.recommended_card_keys.map(cardName).join(" + ")}
        </p>

        <div className="mt-4 grid grid-cols-2 gap-6 sm:grid-cols-3">
          <Stat label="Portfolio value" value={formatRupees(data.recommended_pv_exact)} emphasis />
          <Stat label="Cards" value={String(data.recommended_size)} />
          {data.robustness && (
            <Stat
              label="Robustness"
              value={data.robustness.robustness !== null ? formatPercent(data.robustness.robustness) : "—"}
            />
          )}
        </div>

        {data.robustness && (
          <p className="mt-4 text-sm text-neutral-600">
            {data.robustness.robustness !== null ? (
              <>
                Keeps <span className="font-medium text-neutral-900">{formatPercent(data.robustness.robustness)}</span>{" "}
                of its value if your spending drops 20%, and{" "}
                {data.robustness.rank_stable ? "stays" : "does not stay"} in the top 3 across low/expected/high
                spend scenarios.
              </>
            ) : (
              "This portfolio has no positive expected value to keep under a lower-spend scenario."
            )}
          </p>
        )}
      </div>

      {data.frontier.length > 1 && <FrontierChart points={data.frontier} recommendedSize={data.recommended_size} />}

      {data.recommendation_steps.length > 0 && (
        <div className="rounded-lg border border-neutral-200 p-6">
          <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-neutral-500">
            Size recommendation
          </h2>
          <ul className="space-y-3">
            {data.recommendation_steps.map((step) => (
              <li key={step.size} className="flex items-start gap-3">
                <span
                  className={
                    step.passes
                      ? "mt-0.5 shrink-0 rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-800"
                      : "mt-0.5 shrink-0 rounded-full bg-neutral-100 px-2 py-0.5 text-xs font-medium text-neutral-500"
                  }
                >
                  {step.size} card{step.size === 1 ? "" : "s"}
                </span>
                <p className="text-sm text-neutral-700">{step.explanation}</p>
              </li>
            ))}
          </ul>
        </div>
      )}

      {(data.classification_owned.length > 0 || data.classification_candidates.length > 0) && (
        <div className="rounded-lg border border-neutral-200 p-6">
          <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-neutral-500">
            Classification
          </h2>
          <ul className="space-y-2">
            {[...data.classification_owned, ...data.classification_candidates].map((c) => (
              <ClassificationRow
                key={c.card_key}
                entry={c}
                name={cardName(c.card_key)}
                downgradeToName={c.downgrade_to ? cardName(c.downgrade_to) : null}
              />
            ))}
          </ul>
        </div>
      )}

      {data.excluded_cards.length > 0 && (
        <details className="rounded-lg border border-neutral-200 p-6 text-sm text-neutral-600">
          <summary className="cursor-pointer font-medium text-neutral-700">
            {data.excluded_cards.length} card{data.excluded_cards.length === 1 ? "" : "s"} couldn&apos;t be
            evaluated
          </summary>
          <ul className="mt-3 space-y-1">
            {data.excluded_cards.map((e) => (
              <li key={e.card_key}>
                <span className="font-medium text-neutral-800">{cardName(e.card_key)}</span>: {e.reason}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

// optimiser/classify.py's own label vocabulary is 7 values, not the 6
// Part F §F.2.2 names (KEEP/OPTIONAL/CLOSE/HOLD/ADD/DOWNGRADE) -- it also
// emits NOT_MATERIAL (a candidate whose ICV, even if positive, doesn't
// clear the materiality bar). Confirmed directly against classify.py
// rather than trusting the spec summary, since a real run surfaced it
// immediately. Listed explicitly here (not left to the neutral fallback
// below, even though it'd render the same by coincidence) so a reader
// doesn't have to guess whether the omission was deliberate.
const LABEL_STYLES: Record<string, string> = {
  KEEP: "bg-green-100 text-green-800",
  ADD: "bg-blue-100 text-blue-800",
  OPTIONAL: "bg-neutral-100 text-neutral-600",
  NOT_MATERIAL: "bg-neutral-100 text-neutral-500",
  CLOSE: "bg-red-100 text-red-800",
  HOLD: "bg-amber-100 text-amber-800",
  DOWNGRADE: "bg-amber-100 text-amber-800",
};

function ClassificationRow({
  entry,
  name,
  downgradeToName,
}: {
  entry: CardClassificationEntry;
  name: string;
  downgradeToName: string | null;
}) {
  return (
    <li className="flex items-center justify-between gap-3 border-t border-neutral-100 pt-2 first:border-t-0 first:pt-0">
      <div className="flex items-center gap-2">
        <span
          className={`rounded-full px-2 py-0.5 text-xs font-medium ${LABEL_STYLES[entry.label] ?? "bg-neutral-100 text-neutral-600"}`}
        >
          {titleCase(entry.label)}
        </span>
        <span className="text-sm text-neutral-800">{name}</span>
        {downgradeToName && <span className="text-xs text-neutral-400">→ {downgradeToName}</span>}
      </div>
      <span className="text-sm font-medium text-neutral-900">ICV {formatRupees(entry.icv)}</span>
    </li>
  );
}

function FrontierChart({ points, recommendedSize }: { points: FrontierPointEntry[]; recommendedSize: number }) {
  const sorted = [...points].sort((a, b) => a.size - b.size);
  const max = Math.max(...sorted.map((p) => Number(p.pv_exact)), 1);

  return (
    <div className="rounded-lg border border-neutral-200 p-6">
      <h2 className="mb-4 text-xs font-semibold uppercase tracking-wide text-neutral-500">
        Value by portfolio size
      </h2>
      <div className="space-y-2">
        {sorted.map((point) => {
          const pct = Math.max((Number(point.pv_exact) / max) * 100, 2);
          const isRecommended = point.size === recommendedSize;
          return (
            <div key={point.size} className="flex items-center gap-3">
              <span className="w-14 shrink-0 text-sm text-neutral-500">
                {point.size} card{point.size === 1 ? "" : "s"}
              </span>
              <div className="h-6 flex-1 rounded bg-neutral-100">
                <div
                  className={isRecommended ? "h-6 rounded bg-neutral-900" : "h-6 rounded bg-neutral-400"}
                  style={{ width: `${pct}%` }}
                />
              </div>
              <span className="w-24 shrink-0 text-right text-sm font-medium text-neutral-900">
                {formatRupees(point.pv_exact)}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
