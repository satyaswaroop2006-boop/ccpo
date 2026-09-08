import { listCards } from "@/lib/api";
import { CalculatorForm } from "./CalculatorForm";

// Part F §F.2.2's Calculator, single-card mode (Slice 3, §F.8). Server
// Component: the card list (for the picker) is fetched here, server-side,
// same as the Catalog screen -- only the interactive spend-row form
// itself needs to be a Client Component (CalculatorForm).
export const dynamic = "force-dynamic"; // same build-time-API-dependency fix as the Catalog page (§164)

export const metadata = {
  title: "Calculator | CCPO",
  description: "See a card's real reward value for your own spending.",
};

export default async function CalculatorPage() {
  const cards = await listCards();

  return (
    <main className="mx-auto w-full max-w-3xl flex-1 px-6 py-12">
      <header className="mb-8">
        <h1 className="text-3xl font-semibold tracking-tight text-neutral-900">
          Calculator
        </h1>
        <p className="mt-2 text-neutral-600">
          Enter your annual spend by category and see one card&apos;s real
          reward value -- computed by the same engine that evaluates every
          card in the catalog, not estimated here.
        </p>
      </header>

      <CalculatorForm cards={cards} />
    </main>
  );
}
