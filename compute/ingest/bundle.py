"""Ingestion bundle loading (Part I SS I.2).

An ingestion bundle is the JSON file format `compute/ingestion/*.json`
uses (data, not code -- kept separate from this package, which is code
only, mirroring how `compute/goldens/` is data for `compute/engine/`).
A bundle extends the same shape `engine.card_bundle.bundle_from_dict`
already parses, with every rule-bearing object carrying a source
citation. This module is the thin loading/reshaping layer `ingest lint`
(and eventually `ingest link`) share -- it computes no rupee value
anywhere (CLAUDE.md rule 1); everything here is pure dict-walking.

**Naming reconciliation** (docs/DECISIONS.md): Part I SS I.2's own worked
example uses `"source_refs": [...]` (a list) per entity, and `"sources":
{...}` at the top level. The one real bundle drafted against this spec,
`bundle_sbi_cashback.json`, independently settled on `"_source": "..."`
(a single string, underscore-prefixed, matching this repo's existing
`_note`/`_engine_compatibility_note` convention for informational
fields) and `"_sources": {...}` at the top level -- and that bundle was
already reviewed and partly approved by Satya before this tool existed.
Rather than forcing a fourth edit of an already-approved artifact to
match a spec detail that turned out not to match how the format was
actually used, both spellings are accepted here: `_source`/`_sources`
are treated as sugar for a one-element `source_refs`/for `sources`.
Part I SS I.2 itself should be updated to document this convention;
noted, not yet done -- see docs/DECISIONS.md.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Every top-level bundle key naming a list (or dict, for `benefits`) of
# rule-bearing entities Part I SS I.4's LINT stage must provenance-check.
# Matches seeds/seed.py's own insertion order (not load-bearing here,
# just keeps this list and that one readable side by side).
ENTITY_LIST_KEYS = ("earning_rules", "caps", "thresholds", "exclusions", "benefits", "surcharges")

# `source_links.entity_type`'s CHECK vocabulary (0001_init.sql), keyed by
# the bundle list this module already walks -- `ingest link` (Part I SS
# I.9) needs this to label each source_links row; lint doesn't, since it
# never writes to the database.
ENTITY_TYPE_BY_LIST_KEY = {
    "earning_rules": "earning_rule", "caps": "cap", "thresholds": "threshold",
    "exclusions": "exclusion", "benefits": "benefit", "surcharges": "surcharge",
}

# Part I SS I.1's own source_type -> evidentiary-weight table, restated as
# a mechanical default for `source_links.confidence` at LINK time. SS I.5
# defines confidence as ALSO depending on whether the transcription itself
# required interpretation ("an unambiguous direct transcription" vs "minor
# interpretation" vs "real interpretive judgement") -- a per-FIELD judgment
# call no bundle drafted so far records explicitly (no bundle has a
# `confidence` field of its own), and not something a mechanical tool can
# infer from the source_type alone. This mapping covers only the
# source-type half of SS I.5's definition; a human reviewer can still
# raise or lower it before publish (only PUBLISHED rows are immutable) --
# it is a starting default, not a substitute for the judgment call.
DEFAULT_CONFIDENCE_BY_SOURCE_TYPE = {
    "mitc": "high", "fee_schedule": "high",
    "official_pdf": "medium", "reward_terms": "medium", "product_page": "medium",
    "network_benefits": "medium", "transfer_partner_doc": "medium",
    "faq": "low", "third_party": "low",
}


def default_confidence_for_source_type(source_type: str) -> str:
    return DEFAULT_CONFIDENCE_BY_SOURCE_TYPE.get(source_type, "low")


@dataclass(frozen=True)
class CitedEntity:
    """One rule-bearing object plus a human-readable path to it, for lint
    reporting -- not an engine type, purely for messages a drafter can
    act on without re-deriving which object is being complained about."""

    path: str
    raw: dict[str, Any]


def load_ingestion_bundle(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text())


def source_refs(entity: dict[str, Any]) -> tuple[str, ...]:
    """Normalises `source_refs` (list, SS I.2's own spelling) and
    `_source` (single string, the real bundle's spelling) -- see module
    docstring. Returns `()` when neither is present, never guesses."""
    if "source_refs" in entity:
        refs = entity["source_refs"]
        if not isinstance(refs, list):
            raise ValueError(f"source_refs must be a list, got {type(refs).__name__}: {refs!r}")
        return tuple(refs)
    if "_source" in entity:
        return (entity["_source"],)
    return ()


def declared_sources(bundle: dict[str, Any]) -> dict[str, dict]:
    """The bundle's own `sources` (SS I.2) or `_sources` (the real
    bundle's spelling) block -- same reconciliation as `source_refs`/
    `_source` above."""
    if "sources" in bundle:
        return bundle["sources"]
    if "_sources" in bundle:
        return bundle["_sources"]
    return {}


def citable_entities(bundle: dict[str, Any]) -> tuple[CitedEntity, ...]:
    """Every object Part I SS I.4's provenance-completeness check must
    inspect: card_version fees, each earning_rule/cap/threshold/
    exclusion/benefit/surcharge, and any reward_currency/redemption_route
    the bundle declares (SS I.2's own list, including its point that
    routes are their own citable entity, separate from their currency).
    `benefits` may be a list (this repo's convention so far) or a dict
    keyed by key (`engine.card_bundle.bundle_from_dict`'s own Benefit map
    shape) -- both accepted."""
    entities: list[CitedEntity] = []

    if "version" in bundle:
        entities.append(CitedEntity(path="version", raw=bundle["version"]))

    for list_key in ENTITY_LIST_KEYS:
        items = bundle.get(list_key, [])
        if isinstance(items, dict):
            items = list(items.values())
        for i, item in enumerate(items):
            label = item.get("key", i)
            entities.append(CitedEntity(path=f"{list_key}[{i}] ({label})", raw=item))

    for i, currency in enumerate(bundle.get("currencies", [])):
        label = currency.get("key", i)
        entities.append(CitedEntity(path=f"currencies[{i}] ({label})", raw=currency))
        for j, route in enumerate(currency.get("routes", [])):
            route_label = route.get("key", j)
            entities.append(CitedEntity(path=f"currencies[{i}].routes[{j}] ({route_label})", raw=route))

    return tuple(entities)
