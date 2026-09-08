import Link from "next/link";
import { notFound } from "next/navigation";
import type { ReactNode } from "react";
import { CardNotFoundError, getCard, type RuleEntry } from "@/lib/api";
import { formatAccrualRate, formatRupees, selectorSummary, titleCase } from "@/lib/format";

// Part F §F.2.1's Catalog detail view (Slice 2, §F.8): "a direct,
// formatted dump of the rule vocabulary Part C defines" -- every section
// below renders one of CardDetailOut's own arrays, unmodified, never
// re-deriving a number GET /cards/{key} didn't already return (§F.0).
export async function generateMetadata(props: PageProps<"/cards/[card_key]">) {
  const { card_key } = await props.params;
  try {
    const card = await getCard(card_key);
    return { title: `${card.name} | CCPO`, description: `${card.name} -- rules, fees, and benefits.` };
  } catch {
    return { title: "Card not found | CCPO" };
  }
}

export default async function CardDetailPage(props: PageProps<"/cards/[card_key]">) {
  const { card_key } = await props.params;

  let card;
  try {
    card = await getCard(card_key);
  } catch (err) {
    if (err instanceof CardNotFoundError) notFound();
    throw err;
  }

  return (
    <main className="mx-auto w-full max-w-3xl flex-1 px-6 py-12">
      <Link href="/" className="text-sm text-neutral-500 hover:text-neutral-800">
        ← All cards
      </Link>

      <header className="mt-4 mb-8">
        <div className="flex items-start justify-between gap-3">
          <h1 className="text-3xl font-semibold tracking-tight text-neutral-900">
            {card.name}
          </h1>
          <span className="shrink-0 rounded-full bg-neutral-100 px-2 py-0.5 text-xs font-medium uppercase text-neutral-600">
            {card.network}
          </span>
        </div>
        <p className="mt-1 text-neutral-500">{card.issuer_name}</p>
      </header>

      <section className="mb-8 grid grid-cols-2 gap-4 rounded-lg border border-neutral-200 p-5 sm:grid-cols-4">
        <Stat label="Joining fee" value={formatRupees(card.joining_fee)} />
        <Stat label="Annual fee" value={formatRupees(card.annual_fee)} />
        <Stat label="Tier" value={card.tier ? titleCase(card.tier) : "—"} />
        <Stat label="Segment" value={card.segment ? titleCase(card.segment) : "—"} />
      </section>

      <RuleSection title="Earning rules" entries={card.earning_rules}>
        {(rule) => (
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="font-medium text-neutral-900">
                {selectorSummary(rule.selector as Record<string, unknown>) ?? "All spend"}
              </p>
              {rule.priority != null && (
                <p className="text-xs text-neutral-400">priority {String(rule.priority)}</p>
              )}
            </div>
            <p className="shrink-0 font-medium text-neutral-900">
              {formatAccrualRate(rule.accrual as Record<string, unknown>)}
            </p>
          </div>
        )}
      </RuleSection>

      <RuleSection title="Caps" entries={card.caps}>
        {(cap) => (
          <div className="flex items-center justify-between gap-3">
            <p className="text-neutral-700">{titleCase(String(cap.key))}</p>
            <p className="font-medium text-neutral-900">
              {String(cap.amount)} / {titleCase(String((cap.window as Record<string, unknown> | undefined)?.kind ?? ""))}
            </p>
          </div>
        )}
      </RuleSection>

      <RuleSection title="Thresholds" entries={card.thresholds}>
        {(threshold) => (
          <div>
            <p className="font-medium text-neutral-900">{titleCase(String(threshold.key))}</p>
            <ul className="mt-1 space-y-1 text-neutral-600">
              {((threshold.tiers as Record<string, unknown>[] | undefined) ?? []).map((tier, i) => (
                <li key={i} className="flex justify-between">
                  <span>{formatRupees(String(tier.threshold_amount))}+</span>
                  <span>{titleCase(String((tier.payload as Record<string, unknown> | undefined)?.type ?? ""))}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </RuleSection>

      <RuleSection title="Exclusions" entries={card.exclusions}>
        {(exclusion) => (
          <p className="text-neutral-700">
            {selectorSummary(exclusion.selector as Record<string, unknown>) ?? titleCase(String(exclusion.key))}
            {" "}excluded from {((exclusion.excluded_from as string[] | undefined) ?? []).join(", ")}
          </p>
        )}
      </RuleSection>

      <RuleSection title="Benefits" entries={card.benefits}>
        {(benefit) => (
          <div className="flex items-center justify-between gap-3">
            <p className="text-neutral-700">{titleCase(String(benefit.key))}</p>
            <p className="font-medium text-neutral-900">
              {benefit.face_value != null
                ? formatRupees(String(benefit.face_value))
                : benefit.entitlement != null
                  ? `${benefit.entitlement} ${benefit.unit_label ?? ""}`.trim()
                  : titleCase(String(benefit.kind))}
            </p>
          </div>
        )}
      </RuleSection>

      <RuleSection title="Surcharges" entries={card.surcharges}>
        {(surcharge) => (
          <p className="text-neutral-700">
            {titleCase(String(surcharge.key))} -- {String((Number(surcharge.rate) * 100).toFixed(2))}%
          </p>
        )}
      </RuleSection>
    </main>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-neutral-400">{label}</dt>
      <dd className="mt-0.5 font-medium text-neutral-900">{value}</dd>
    </div>
  );
}

function RuleSection({
  title,
  entries,
  children,
}: {
  title: string;
  entries: RuleEntry[];
  children: (entry: RuleEntry) => ReactNode;
}) {
  if (entries.length === 0) return null;
  return (
    <section className="mb-8">
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-neutral-500">
        {title}
      </h2>
      <ul className="space-y-3 rounded-lg border border-neutral-200 p-5">
        {entries.map((entry, i) => (
          <li key={i} className={i > 0 ? "border-t border-neutral-100 pt-3" : undefined}>
            {children(entry)}
          </li>
        ))}
      </ul>
    </section>
  );
}
