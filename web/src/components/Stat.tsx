// Shared label/value tile, used by both the single-card and portfolio
// result panels (Slice 3 + 4, Part F §F.2.2). Extracted here rather than
// left local to one panel, specifically so neither result-panel file
// needs to import from the other (CalculatorForm renders
// OptimiseResultPanel; a Stat defined in CalculatorForm and imported back
// from OptimiseResultPanel would be a circular import).
export function Stat({ label, value, emphasis }: { label: string; value: string; emphasis?: boolean }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-neutral-400">{label}</dt>
      <dd className={emphasis ? "mt-0.5 text-lg font-semibold text-neutral-900" : "mt-0.5 font-medium text-neutral-900"}>
        {value}
      </dd>
    </div>
  );
}
