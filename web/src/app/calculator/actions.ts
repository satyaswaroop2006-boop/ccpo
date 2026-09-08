"use server";

import { evaluateCard, getCard, type EvaluateResponse, type SpendItemIn } from "@/lib/api";

export type RunEvaluateResult =
  | { ok: true; data: EvaluateResponse }
  | { ok: false; error: string };

/**
 * Part F §F.2.2's Calculator, single-card mode (Slice 3, §F.8). A Server
 * Action, not a route handler: the client component holds spend-row state
 * and calls this directly with a plain object (the documented pattern for
 * invoking a Server Function from a Client Component's event handler --
 * see next/dist/docs/.../use-server.md's own `fetchUsers`/`MyButton`
 * example), so there's no new HTTP endpoint to keep in sync with
 * app/main.py.
 *
 * Auto-supplies `benefit_need`/`benefit_unit_value` = 0 for every
 * COUNTABLE benefit on the card (e.g. Priority Pass lounge visits) --
 * `evaluate_card` raises a ValueError (-> HTTP 422) if a countable
 * benefit has no explicit need/unit_value assumption, and most real
 * cards ingested this session (the whole PRIME family, ELITE) have one.
 * This is a SCENARIO CHOICE, the same posture every golden's own
 * `benefit_need=0` note already takes in this repo ("assume this
 * cardholder doesn't use lounge access... not a claim about the real
 * entitlement") -- surfaced to the user via `benefit_value` in the
 * result (always 0 for now), not hidden. A full "how many times will you
 * use this benefit" input is a later slice's work, not invented here to
 * keep this one minimal.
 */
export async function runEvaluate(cardKey: string, spend: SpendItemIn[]): Promise<RunEvaluateResult> {
  if (spend.length === 0) {
    return { ok: false, error: "Add at least one spend category before calculating." };
  }

  try {
    const card = await getCard(cardKey);
    const countableBenefitKeys = card.benefits
      .filter((b) => b.kind === "countable")
      .map((b) => b.key);

    const benefit_need: Record<string, string> = {};
    const benefit_unit_value: Record<string, string> = {};
    for (const key of countableBenefitKeys) {
      benefit_need[key] = "0";
      benefit_unit_value[key] = "0";
    }

    const data = await evaluateCard(cardKey, spend, { benefit_need, benefit_unit_value });
    return { ok: true, data };
  } catch (err) {
    if (err instanceof Error) {
      return { ok: false, error: err.message };
    }
    return { ok: false, error: "Something went wrong evaluating this card." };
  }
}
