"""Part I SS I.9's `ingest review-queue` tool.

Lists `source_links` where `reviewer_status='unreviewed'`, grouped by
card -- "a thin CLI over `idx_slinks_review`, already indexed for exactly
this... the MVP-scale version" of the query a future Part F review UI
will run properly. Read-only: this module never writes to the database.

Part I SS I.5 is explicit that the actual approve/reject flip is a human
act, "never set by Claude, never set by the same automated step that
drafted the field" -- and SS I.9 only specifies `review-queue` as a
LISTING tool, not a mutation one. Deliberately not extended with an
`approve`/`reject` subcommand here: Supabase's own Table Editor already
lets a non-technical reviewer flip `source_links.reviewer_status` with a
dropdown, no SQL required, so there's no missing capability this would
fill -- only scope beyond what SS I.9 actually asked for.

`source_links` is a soft-polymorphic link (Part D Decision 4: `entity_
type`/`entity_id`, not FK-enforced) -- resolving "which card does this
row belong to" means a separate lookup per `entity_type`, since each one
points at a different table. `reward_currency`/`redemption_route` are
the one exception: per Part D's own table map they hang off `issuers`
directly, not `card_versions` (Part I SS I.2: "a currency shared across
several of an issuer's cards is drafted once... not re-declared per
card") -- so they're grouped by ISSUER here, not by card, since a shared
currency genuinely doesn't belong to one card more than another.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import psycopg

# entity_type -> (table, join column back to card_versions)
_CARD_CHILD_TABLES = {
    "earning_rule": "earning_rules",
    "cap": "caps",
    "threshold": "thresholds",
    "exclusion": "exclusions",
    "benefit": "benefits",
    "surcharge": "surcharges",
}

# entity_type -> table, for the generic "drafted value" row fetch below --
# a superset of _CARD_CHILD_TABLES (adds the two entity types that don't
# hang off a card_version at all, per this module's own docstring).
_ENTITY_TABLES = {
    "card_version": "card_versions",
    "reward_currency": "reward_currencies",
    "redemption_route": "redemption_routes",
    **_CARD_CHILD_TABLES,
}


def _jsonable_field(value: Any) -> Any:
    """Same Decimal/date-safety `app/schemas.py::_jsonable` already applies
    to engine dataclasses, applied here to a raw DB row instead -- `jsonb`
    columns (`selector`, `accrual`, `window_def`, ...) already come back
    from psycopg3 as plain dict/list, so they pass through unchanged."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (_dt.date, _dt.datetime)):
        return value.isoformat()
    return value


def _fetch_entity_fields(cur, entity_type: str, entity_id: Any) -> dict[str, Any]:
    """Part F §F.2.3's "the field's own value as drafted" -- a reviewer
    can't approve/reject a citation without seeing what it's citing.
    Fetched generically (`select *`, keyed by the row's own column names)
    rather than a hand-written field list per entity_type: the same
    "discover generically, don't hardcode a type-specific list" posture
    docs/DECISIONS.md #166 already used for countable-benefit keys --
    stays correct if a table gains a column later, a hardcoded list
    wouldn't. `id`/`card_version_id` are dropped: both are already
    surfaced elsewhere (`source_link_id`, the group's own label), not
    part of "the value as drafted"."""
    table = _ENTITY_TABLES.get(entity_type)
    if table is None:
        return {}
    cur.execute(f"select * from {table} where id = %s", (entity_id,))
    row = cur.fetchone()
    if row is None:
        return {}
    columns = [desc.name for desc in cur.description]
    return {
        col: _jsonable_field(val)
        for col, val in zip(columns, row)
        if col not in ("id", "card_version_id")
    }


@dataclass(frozen=True)
class ReviewQueueItem:
    source_link_id: str
    entity_type: str
    entity_key: str
    entity_fields: dict[str, Any]
    confidence: str
    source_url: str
    source_type: str
    source_title: str | None
    source_storage_path: str | None


@dataclass(frozen=True)
class ReviewQueueGroup:
    label: str  # "card:<key>" or "issuer:<key> (shared currency)"
    card_version_id: str | None  # the id `ingest publish` needs -- None for issuer-level (currency) groups
    items: tuple[ReviewQueueItem, ...]


def _label_for_card_version(cur, card_version_id: Any) -> tuple[str, Any]:
    cur.execute(
        "select c.key from card_versions cv join cards c on c.id = cv.card_id where cv.id = %s",
        (card_version_id,),
    )
    row = cur.fetchone()
    label = f"card:{row[0]}" if row else f"card_version:{card_version_id} (card not found)"
    return label, card_version_id


def _label_for_child(cur, table: str, entity_id: Any) -> tuple[str, Any]:
    cur.execute(
        f"select c.key, cv.id from {table} t join card_versions cv on cv.id = t.card_version_id"
        " join cards c on c.id = cv.card_id where t.id = %s",
        (entity_id,),
    )
    row = cur.fetchone()
    if row is None:
        return f"{table}:{entity_id} (card not found)", None
    card_key, cv_id = row
    return f"card:{card_key}", cv_id


def _label_for_currency(cur, entity_id: Any) -> str:
    cur.execute(
        "select i.key from reward_currencies rcy join issuers i on i.id = rcy.issuer_id where rcy.id = %s",
        (entity_id,),
    )
    row = cur.fetchone()
    return f"issuer:{row[0]} (shared currency)" if row else f"reward_currency:{entity_id} (issuer not found)"


def _label_for_route(cur, entity_id: Any) -> str:
    cur.execute(
        "select i.key from redemption_routes rr"
        " join reward_currencies rcy on rcy.id = rr.currency_id"
        " join issuers i on i.id = rcy.issuer_id where rr.id = %s",
        (entity_id,),
    )
    row = cur.fetchone()
    return f"issuer:{row[0]} (shared currency)" if row else f"redemption_route:{entity_id} (issuer not found)"


# entity_type -> resolver returning (label, card_version_id_or_None)
def _resolve(cur, entity_type: str, entity_id: Any) -> tuple[str, Any]:
    if entity_type == "card_version":
        return _label_for_card_version(cur, entity_id)
    if entity_type in _CARD_CHILD_TABLES:
        return _label_for_child(cur, _CARD_CHILD_TABLES[entity_type], entity_id)
    if entity_type == "reward_currency":
        return _label_for_currency(cur, entity_id), None
    if entity_type == "redemption_route":
        return _label_for_route(cur, entity_id), None
    return f"(unknown entity_type {entity_type!r})", None


def _entity_key(cur, entity_type: str, entity_id: Any) -> str:
    if entity_type == "card_version":
        return "(card_version)"
    table = _CARD_CHILD_TABLES.get(entity_type) or {
        "reward_currency": "reward_currencies", "redemption_route": "redemption_routes",
    }.get(entity_type)
    if table is None:
        return f"(unknown entity_type {entity_type!r})"
    cur.execute(f"select key from {table} where id = %s", (entity_id,))
    row = cur.fetchone()
    return row[0] if row else "(deleted)"


def build_review_queue(conn: psycopg.Connection) -> tuple[ReviewQueueGroup, ...]:
    with conn.cursor() as cur:
        cur.execute(
            "select sl.id, sl.entity_type, sl.entity_id, sl.confidence,"
            " s.url, s.source_type, s.title, s.storage_path"
            " from source_links sl join sources s on s.id = sl.source_id"
            " where sl.reviewer_status = 'unreviewed' order by sl.created_at",
        )
        rows = cur.fetchall()

        grouped: dict[str, list[ReviewQueueItem]] = {}
        cv_id_by_label: dict[str, Any] = {}
        for sl_id, entity_type, entity_id, confidence, url, source_type, title, storage_path in rows:
            label, cv_id = _resolve(cur, entity_type, entity_id)
            cv_id_by_label[label] = cv_id

            entity_key = _entity_key(cur, entity_type, entity_id)
            entity_fields = _fetch_entity_fields(cur, entity_type, entity_id)
            item = ReviewQueueItem(
                source_link_id=str(sl_id), entity_type=entity_type, entity_key=entity_key,
                entity_fields=entity_fields, confidence=confidence, source_url=url,
                source_type=source_type, source_title=title, source_storage_path=storage_path,
            )
            grouped.setdefault(label, []).append(item)

    return tuple(
        ReviewQueueGroup(
            label=label, card_version_id=str(cv_id_by_label[label]) if cv_id_by_label[label] is not None else None,
            items=tuple(items),
        )
        for label, items in grouped.items()
    )
