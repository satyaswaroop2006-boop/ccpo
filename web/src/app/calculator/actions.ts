"use server";

import { evaluateCard, getCard, listCards, optimiseCards, type EvaluateResponse, type OptimiseResponse, type SpendItemIn } from "@/lib/api";

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

export type RunOptimiseResult =
  | { ok: true; data: OptimiseResponse }
  | { ok: false; error: string };

/**
 * Part F §F.2.2's Calculator, portfolio mode (Slice 4, §F.8). Same Server
 * Action pattern as `runEvaluate` -- no new endpoint, `/optimise` already
 * returns everything this screen renders (frontier, checklist,
 * classification, robustness).
 *
 * The SAME countable-benefit problem `runEvaluate` solves applies here,
 * one level up: `/optimise`'s own pre-flight compatibility probe
 * (`_partition_universe`, app/main.py) EXCLUDES rather than crashes on a
 * card with no benefit_need/benefit_unit_value for a countable benefit --
 * so without this, every PRIME-family card, ELITE, and the synthetic
 * `syn_lounge` fixture would silently drop out of consideration entirely
 * (visible only via `excluded_cards`, easy to miss), not fail loudly.
 * Rather than hardcoding the two keys currently in the catalog
 * (`priority_pass_lounge`, `dom_lounge` -- checked directly against the
 * live DB while building this), this discovers them the same way
 * `runEvaluate` discovers one card's own: fetch every card's own detail
 * and collect every COUNTABLE benefit key across the whole universe. One
 * extra round of parallel `GET /cards/{key}` calls per optimise request,
 * negligible next to the solve itself (Part E SS E.13's own budget: up to
 * 30s cold for the sweep alone) -- and it stays correct if a third
 * countable-benefit card is ever published, which a hardcoded pair
 * wouldn't.
 */
export async function runOptimise(spend: SpendItemIn[]): Promise<RunOptimiseResult> {
  if (spend.length === 0) {
    return { ok: false, error: "Add at least one spend category before calculating." };
  }

  try {
    const summaries = await listCards();
    const details = await Promise.all(summaries.map((s) => getCard(s.card_key)));

    const countableBenefitKeys = new Set<string>();
    for (const card of details) {
      for (const b of card.benefits) {
        if (b.kind === "countable") countableBenefitKeys.add(b.key);
      }
    }

    const benefit_need: Record<string, string> = {};
    const benefit_unit_value: Record<string, string> = {};
    for (const key of countableBenefitKeys) {
      benefit_need[key] = "0";
      benefit_unit_value[key] = "0";
    }

    const data = await optimiseCards(spend, { benefit_need, benefit_unit_value });
    return { ok: true, data };
  } catch (err) {
    if (err instanceof Error) {
      return { ok: false, error: err.message };
    }
    return { ok: false, error: "Something went wrong building a portfolio recommendation." };
  }
}
