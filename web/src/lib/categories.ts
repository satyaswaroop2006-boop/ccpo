/**
 * Spend category vocabulary -- mirrors the keys of
 * engine/normalise.py::DEFAULT_TICKET_SIZES exactly (that dict's own
 * comment already flags several of these as "ticket-size guesses...
 * needs Satya's sign-off," but the CATEGORY NAMES themselves are Part
 * C's own vocabulary, not something this file invents). Hand-copied
 * rather than served from a new endpoint -- Part F §F.3 deliberately
 * scoped Slice 3 to "no new API need" for the Calculator, and this list
 * changes only when Part C's own category vocabulary does, same
 * cadence as `src/lib/api.ts`'s own hand-typed schema mirrors.
 */
export const CATEGORIES = [
  "grocery",
  "quick_commerce",
  "dining",
  "food_delivery",
  "ecommerce",
  "offline_retail",
  "utilities",
  "entertainment",
  "domestic_flights",
  "international_flights",
  "hotels_domestic",
  "fuel",
  "insurance",
  "rent",
  "education",
  "wallet",
  "jewelry",
  "gift_novelty",
  "railways",
  "quasi_cash",
  "digital_gaming",
  "tolls",
  "government",
] as const;

export type Category = (typeof CATEGORIES)[number];
