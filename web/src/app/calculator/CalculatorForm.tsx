"use client";

import { useState, useTransition, type FormEvent, type ReactNode } from "react";
import type { CardSummary } from "@/lib/api";
import { CATEGORIES } from "@/lib/categories";
import { formatRupees, titleCase } from "@/lib/format";
import { runEvaluate, runOptimise, type RunEvaluateResult, type RunOptimiseResult } from "./actions";
import { OptimiseResultPanel } from "./OptimiseResultPanel";
import { Stat } from "@/components/Stat";

type Mode = "single" | "portfolio";

interface SpendRow {
  id: string;
  category: string;
  annualAmount: string;
  geography: "domestic" | "international";
}

function newRow(): SpendRow {
  return { id: crypto.randomUUID(), category: CATEGORIES[0], annualAmount: "", geography: "domestic" };
}

export function CalculatorForm({ cards }: { cards: CardSummary[] }) {
  const [mode, setMode] = useState<Mode>("single");
  const [cardKey, setCardKey] = useState(cards[0]?.card_key ?? "");
  const [rows, setRows] = useState<SpendRow[]>([newRow()]);
  const [evaluateResult, setEvaluateResult] = useState<RunEvaluateResult | null>(null);
  const [optimiseResult, setOptimiseResult] = useState<RunOptimiseResult | null>(null);
  const [isPending, startTransition] = useTransition();

  function updateRow(id: string, patch: Partial<SpendRow>) {
    setRows((prev) => prev.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  }

  function removeRow(id: string) {
    setRows((prev) => (prev.length > 1 ? prev.filter((r) => r.id !== id) : prev));
  }

  function handleModeChange(next: Mode) {
    // Switching modes clears the OTHER mode's stale result -- keeping
    // an old single-card result visible after switching to Portfolio
    // (or vice versa) would read as if it were still live.
    setMode(next);
    setEvaluateResult(null);
    setOptimiseResult(null);
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const spend = rows
      .filter((r) => r.annualAmount.trim() !== "")
      .map((r) => ({ category: r.category, annual_amount: r.annualAmount, geography: r.geography }));

    startTransition(async () => {
      if (mode === "single") {
        setEvaluateResult(await runEvaluate(cardKey, spend));
      } else {
        setOptimiseResult(await runOptimise(spend));
      }
    });
  }

  return (
    <div className="space-y-8">
      <form onSubmit={handleSubmit} className="space-y-6">
        <div className="inline-flex rounded-md border border-neutral-300 p-0.5">
          <ModeButton active={mode === "single"} onClick={() => handleModeChange("single")}>
            Single card
          </ModeButton>
          <ModeButton active={mode === "portfolio"} onClick={() => handleModeChange("portfolio")}>
            Portfolio
          </ModeButton>
        </div>

        {mode === "single" && (
          <div>
            <label className="block text-sm font-medium text-neutral-700" htmlFor="card">
              Card
            </label>
            <select
              id="card"
              value={cardKey}
              onChange={(e) => setCardKey(e.target.value)}
              className="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-neutral-900"
            >
              {cards.map((c) => (
                <option key={c.card_key} value={c.card_key}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>
        )}

        <div>
          <div className="mb-2 flex items-center justify-between">
            <span className="block text-sm font-medium text-neutral-700">
              Annual spend by category
            </span>
            <button
              type="button"
              onClick={() => setRows((prev) => [...prev, newRow()])}
              className="text-sm font-medium text-neutral-600 hover:text-neutral-900"
            >
              + Add category
            </button>
          </div>

          <div className="space-y-2">
            {rows.map((row) => (
              // flex-wrap, not a fixed-width single line: on a narrow
              // (mobile) viewport, a category select long enough to show
              // "International Flights" plus an amount input, a geography
              // select, and a Remove button never fit on one line -- the
              // row used to overflow the viewport horizontally instead of
              // wrapping, found by actually checking a mobile screenshot,
              // not assumed fine from the desktop layout.
              <div key={row.id} className="flex flex-wrap items-center gap-2">
                <select
                  value={row.category}
                  onChange={(e) => updateRow(row.id, { category: e.target.value })}
                  className="min-w-0 flex-1 basis-40 rounded-md border border-neutral-300 px-2 py-2 text-sm text-neutral-900"
                >
                  {CATEGORIES.map((cat) => (
                    <option key={cat} value={cat}>
                      {titleCase(cat)}
                    </option>
                  ))}
                </select>

                <div className="flex items-center gap-1">
                  <span className="text-neutral-400">₹</span>
                  <input
                    type="number"
                    min="0"
                    step="1"
                    placeholder="Annual amount"
                    value={row.annualAmount}
                    onChange={(e) => updateRow(row.id, { annualAmount: e.target.value })}
                    className="w-32 rounded-md border border-neutral-300 px-3 py-2 text-sm text-neutral-900"
                  />
                </div>

                <select
                  value={row.geography}
                  onChange={(e) => updateRow(row.id, { geography: e.target.value as SpendRow["geography"] })}
                  className="rounded-md border border-neutral-300 px-2 py-2 text-sm text-neutral-500"
                  title="Geography"
                >
                  <option value="domestic">Domestic</option>
                  <option value="international">International</option>
                </select>

                <button
                  type="button"
                  onClick={() => removeRow(row.id)}
                  disabled={rows.length === 1}
                  className="text-sm text-neutral-400 hover:text-red-600 disabled:cursor-not-allowed disabled:opacity-40 sm:ml-auto"
                  aria-label="Remove category"
                >
                  Remove
                </button>
              </div>
            ))}
          </div>
        </div>

        <button
          type="submit"
          disabled={isPending || (mode === "single" && !cardKey)}
          className="rounded-md bg-neutral-900 px-5 py-2.5 text-sm font-medium text-white hover:bg-neutral-700 disabled:opacity-50"
        >
          {isPending ? "Calculating…" : "Calculate"}
        </button>
      </form>

      {mode === "single" && evaluateResult && <EvaluateResultPanel result={evaluateResult} />}
      {mode === "portfolio" && optimiseResult && (
        <OptimiseResultPanel result={optimiseResult} cards={cards} />
      )}
    </div>
  );
}

function ModeButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={
        active
          ? "rounded px-4 py-1.5 text-sm font-medium bg-neutral-900 text-white"
          : "rounded px-4 py-1.5 text-sm font-medium text-neutral-600 hover:text-neutral-900"
      }
    >
      {children}
    </button>
  );
}

function EvaluateResultPanel({ result }: { result: RunEvaluateResult }) {
  if (!result.ok) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 p-5 text-red-800">
        <p className="font-medium">Couldn&apos;t calculate that.</p>
        <p className="mt-1 text-sm">{result.error}</p>
      </div>
    );
  }

  const { data } = result;
  return (
    <div className="rounded-lg border border-neutral-200 p-6">
      <div className="grid grid-cols-2 gap-6 sm:grid-cols-3">
        <Stat label="NACV, steady state" value={formatRupees(data.nacv.steady_state)} emphasis />
        <Stat label="NACV, year 1" value={formatRupees(data.nacv.year_1)} emphasis />
        <Stat label="Gross reward value" value={formatRupees(data.gross_reward_value)} />
        <Stat label="Benefit value" value={formatRupees(data.benefit_value)} />
        <Stat
          label="Annual fee"
          value={data.waiver_achieved ? "Waived" : formatRupees(data.fee_steady)}
        />
        <Stat label="Year-1 fee" value={formatRupees(data.fee_year1)} />
      </div>

      {data.nacv.trace.length > 0 && (
        <div className="mt-6 border-t border-neutral-100 pt-4">
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-neutral-500">
            Breakdown
          </h3>
          <ul className="space-y-1 text-sm">
            {data.nacv.trace.map((line, i) => (
              <li key={i} className="flex justify-between text-neutral-700">
                <span>{line.label}</span>
                <span className="font-medium text-neutral-900">{formatRupees(line.amount)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
