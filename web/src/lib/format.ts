/**
 * Display-only formatting (Part F §F.0: "the frontend may format, sort,
 * filter, and chart numbers it receives" -- never re-derive one). Every
 * function here takes an already-computed value and changes how it LOOKS,
 * never what it IS.
 */

export function formatRupees(value: string | number): string {
  const n = typeof value === "string" ? Number(value) : value;
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(n);
}

export function titleCase(key: string): string {
  return key
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

/**
 * Renders an Accrual dict (engine/accrue.py::Accrual, via _jsonable) as
 * the same rate a card's own T&C states -- "10 points per ₹100" or
 * "1.5% cashback". Reads fields the API already computed/transcribed
 * (type, unit_amount, points_per_unit, rate); does not compute a rate
 * from anything, only formats the one already present.
 */
export function formatAccrualRate(accrual: Record<string, unknown> | undefined): string {
  if (!accrual) return "—";
  if (accrual.type === "per_unit") {
    return `${accrual.points_per_unit} pts / ₹${accrual.unit_amount}`;
  }
  if (accrual.type === "percentage") {
    // "% of spend", not "% cashback" -- percentage accrual isn't
    // exclusive to cashback currencies (e.g. a points currency could
    // also use a flat percentage rate), so this stays currency-neutral.
    return `${(Number(accrual.rate) * 100).toFixed(2)}% of spend`;
  }
  return String(accrual.type ?? "—");
}

export function selectorSummary(selector: Record<string, unknown> | undefined): string | null {
  if (!selector) return null;
  const categories = selector.categories as string[] | null | undefined;
  if (categories && categories.length > 0) {
    return categories.map(titleCase).join(", ");
  }
  return null; // empty/all-null selector -- matches everything, no summary needed
}
