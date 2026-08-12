"""Card data access (Part E SS E.0's `/evaluate` input boundary).

`CardRepository` is the seam between the API layer and wherever card rule
data actually lives, so `app/main.py`'s handlers never care which one is
behind it. `SyntheticCatalogRepository` (backed by
`seeds/synthetic_cards.py`, the same fixtures the golden battery uses) is
the only implementation today.

A Postgres-backed implementation (reading `card_versions`/`earning_rules`/
`caps`/`thresholds`/`threshold_tiers`/`exclusions`/`benefits`/
`surcharges`/`reward_currencies`/`redemption_routes` per
`supabase/migrations/0001_init.sql`, assembling the same dict shape
`engine/card_bundle.bundle_from_dict` already consumes) is deliberately
NOT built here yet: `compute/.env`'s `DATABASE_URL` doesn't resolve from
this dev sandbox and there is no local Postgres/Docker available to verify
one against, so writing it now would be unverified code shipped blind.
See docs/DECISIONS.md's Phase 3 entry -- it's the explicit next task once
a reachable connection string is confirmed.
"""
from __future__ import annotations

from typing import Protocol

from engine.card_bundle import CardRuleBundle, bundle_from_dict, currencies_from_dicts
from engine.valuation import RewardCurrency
from seeds.synthetic_cards import CARDS, CURRENCIES


class CardNotFoundError(KeyError):
    pass


class CardRepository(Protocol):
    def get_card_bundle(self, card_key: str) -> CardRuleBundle: ...

    def get_currencies(self) -> dict[str, RewardCurrency]: ...


class SyntheticCatalogRepository:
    """Backed by `seeds/synthetic_cards.py` -- the 12 structural test cards
    of Part C SS C.9, same fixtures the golden battery runs against. Not a
    stand-in for real card data (CLAUDE.md rule 4: never source real
    reward data from memory); this is purely what makes `/evaluate` and
    `/next-best-spend` runnable and testable before a database is reachable."""

    def __init__(self) -> None:
        self._cards_by_key = {c["key"]: c for c in CARDS}
        self._currencies = currencies_from_dicts(CURRENCIES)

    def get_card_bundle(self, card_key: str) -> CardRuleBundle:
        card = self._cards_by_key.get(card_key)
        if card is None:
            raise CardNotFoundError(card_key)
        return bundle_from_dict(card)

    def get_currencies(self) -> dict[str, RewardCurrency]:
        return self._currencies
