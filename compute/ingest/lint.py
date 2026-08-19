"""Structural validation for ingestion bundles (Part I SS I.4's LINT stage).

Two checks, both genuinely buildable and genuinely new to this pass:

1. **Provenance completeness** (Part I's own new requirement, SS I.4):
   every rule-bearing entity cites at least one source declared in the
   same bundle.
2. **Engine compatibility**: does the bundle actually translate through
   `engine.card_bundle.bundle_from_dict`/`currencies_from_dicts`, and
   does every selector-bearing object pass the engine's OWN structural
   validators (`match.validate_rule`, `eligibility.validate_exclusion`,
   `costs.validate_surcharge` -- all three promoted from module-private
   to public this same pass, specifically so this tool could call each
   one PER ITEM and collect every issue in one run, rather than relying
   on `match()`/`apply_eligibility()`/`surcharge_cost()`'s own loops,
   which each stop at the first bad rule/exclusion/surcharge they hit)?
   Reuses the engine's own validators rather than re-implementing "which
   selector fields are supported" a second time (CLAUDE.md rule 1: one
   engine, one place that knows what it can evaluate) -- and per docs/
   DECISIONS.md, those three validators only became trustworthy for this
   purpose after a real bug fix this same pass made to `card_bundle.py`'s
   selector loaders (they used to silently drop the very fields these
   validators check for, so the validators never actually fired).

**What this does NOT do, on purpose, not by oversight**: Part C SS C.11's
original four-check battery (selector-overlap linting, threshold-payload
depth check, cap-scope resolution, currency/route completeness) is not
implemented here, or anywhere else in this repo -- confirmed by search
before writing this module, not assumed. `lint_bundle`'s report says so
explicitly (`checks_not_implemented`) rather than silently claiming full
C.11 coverage. See docs/DECISIONS.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from engine.card_bundle import bundle_from_dict, currencies_from_dicts
from engine.costs import validate_surcharge
from engine.eligibility import validate_exclusion
from engine.match import validate_rule
from ingest.bundle import citable_entities, declared_sources, source_refs

Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class LintIssue:
    check: str
    severity: Severity
    entity: str
    message: str


@dataclass(frozen=True)
class LintReport:
    issues: tuple[LintIssue, ...]
    checks_run: tuple[str, ...]
    checks_not_implemented: tuple[str, ...]

    @property
    def errors(self) -> tuple[LintIssue, ...]:
        return tuple(i for i in self.issues if i.severity == "error")

    @property
    def passed(self) -> bool:
        """LINT's own exit condition (SS I.4: "a bundle failing either
        battery does not proceed") -- warnings don't block, errors do."""
        return not self.errors


C11_BATTERY_NOT_IMPLEMENTED = (
    "selector-overlap linting (ambiguous priorities)",
    "threshold-payload depth check (Stage 7's depth-1 invariant)",
    "cap-scope resolution",
    "currency/route completeness (structural cross-checks beyond provenance)",
)


def check_provenance_completeness(bundle: dict[str, Any]) -> list[LintIssue]:
    sources = declared_sources(bundle)
    issues: list[LintIssue] = []
    for entity in citable_entities(bundle):
        refs = source_refs(entity.raw)
        if not refs:
            issues.append(LintIssue(
                check="provenance_completeness", severity="error", entity=entity.path,
                message="no source citation (_source or source_refs) -- every rule-bearing fact needs one, per Part I SS I.0",
            ))
            continue
        unknown = [r for r in refs if r not in sources]
        if unknown:
            issues.append(LintIssue(
                check="provenance_completeness", severity="error", entity=entity.path,
                message=f"cites source key(s) {unknown} not declared in this bundle's sources/_sources block",
            ))
    return issues


def check_engine_compatibility(bundle: dict[str, Any]) -> list[LintIssue]:
    issues: list[LintIssue] = []

    try:
        card_bundle = bundle_from_dict(bundle)
    except Exception as e:
        issues.append(LintIssue(
            check="engine_compatibility", severity="error", entity="(whole bundle)",
            message=f"bundle_from_dict failed to translate this bundle: {type(e).__name__}: {e}",
        ))
        return issues  # nothing further to probe if translation itself failed

    for rule in card_bundle.earning_rules:
        try:
            validate_rule(rule)
        except ValueError as e:
            issues.append(LintIssue(check="engine_compatibility", severity="error", entity=f"earning_rules ({rule.key})", message=str(e)))

    for exclusion in card_bundle.exclusions:
        try:
            validate_exclusion(exclusion)
        except ValueError as e:
            issues.append(LintIssue(check="engine_compatibility", severity="error", entity=f"exclusions ({exclusion.key})", message=str(e)))

    for surcharge in card_bundle.surcharges:
        try:
            validate_surcharge(surcharge)
        except ValueError as e:
            issues.append(LintIssue(check="engine_compatibility", severity="error", entity=f"surcharges ({surcharge.key})", message=str(e)))

    try:
        currencies_from_dicts(list(bundle.get("currencies", [])))
    except Exception as e:
        issues.append(LintIssue(
            check="engine_compatibility", severity="error", entity="currencies",
            message=f"currencies_from_dicts failed: {type(e).__name__}: {e}",
        ))

    return issues


def lint_bundle(bundle: dict[str, Any]) -> LintReport:
    issues = [*check_provenance_completeness(bundle), *check_engine_compatibility(bundle)]
    return LintReport(
        issues=tuple(issues),
        checks_run=("provenance_completeness", "engine_compatibility"),
        checks_not_implemented=C11_BATTERY_NOT_IMPLEMENTED,
    )
