"""`ingest` CLI (Part I SS I.9 plus SS I.1's CAPTURE stage). Five pipeline
subcommands: `capture`, `lint`, `link`, `review-queue`, `publish` -- one per
pipeline stage, run in that order -- plus `reward-catalog-ratio`, a DRAFT-time
helper that is NOT one of the six pipeline stages (see `ingest.reward_catalog_
ratio`'s own module docstring).

`review-queue` is read-only -- it never flips `reviewer_status`. That
flip is a human act SS I.5 requires stay outside any automated tool
("never set by Claude, never set by the same automated step that drafted
the field"); Supabase's own Table Editor already lets a non-technical
reviewer do it with a dropdown, so this CLI doesn't need its own
mutation command to make the workflow usable.

Usage:
    python -m ingest capture compute/ingestion/bundle_sbi_cashback.json
    python -m ingest capture compute/ingestion/bundle_sbi_cashback.json --source reward_terms --file reward_terms=./downloaded.pdf
    DATABASE_URL=postgresql://... python -m ingest capture ... --sync-db
    python -m ingest lint compute/ingestion/bundle_sbi_cashback.json
    DATABASE_URL=postgresql://... python -m ingest link compute/ingestion/bundle_sbi_cashback.json
    DATABASE_URL=postgresql://... python -m ingest review-queue
    DATABASE_URL=postgresql://... python -m ingest publish <card_version_id> --golden compute/ingestion/golden_sbi_cashback.json
    python -m ingest reward-catalog-ratio --card sbi-card-prime --card sbi-card-elite --out compute/ingestion/reference_reward_point_values.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from ingest.bundle import load_ingestion_bundle
from ingest.capture import CaptureError, CaptureResult, capture_bundle, sync_captured_sources_to_db
from ingest.lint import LintReport, lint_bundle
from ingest.link import LinkError, LinkResult, link_bundle
from ingest.publish import PublishError, PublishResult, publish_card_version
from ingest.review import ReviewQueueGroup, build_review_queue
from ingest.reward_catalog_ratio import CatalogFetchError, refresh_reference

load_dotenv()  # compute/.env's DATABASE_URL, local-dev convenience -- same as app/main.py


def _storage_from_env():
    from ingest.storage import SupabaseStorageBackend

    base_url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not base_url or not key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set (see compute/.env.example)")
    return SupabaseStorageBackend(base_url=base_url, service_role_key=key)


def _print_capture_results(results: list[CaptureResult], bundle_path: str) -> None:
    print(f"ingest capture: {bundle_path}")
    if not results:
        print("Nothing to do -- every requested source already has storage_path/captured_at (use --force to re-capture).")
        return
    for r in results:
        print(f"  {r.source_key}: {r.method}, stored at {r.storage_path} (captured_at={r.captured_at})")
        if r.pdf is not None:
            print(f"    pdf: {r.pdf.note}")
        for w in r.warnings:
            print(f"    [warning] {w}")
    print()
    print(f"bundle file updated with the new storage_path/captured_at ({len(results)} source(s)).")


def cmd_capture(args: argparse.Namespace) -> int:
    manual_files: dict[str, Path] = {}
    for spec in args.file or []:
        if "=" not in spec:
            print(f"ingest capture: --file must be KEY=path, got {spec!r}", file=sys.stderr)
            return 2
        key, _, path = spec.partition("=")
        manual_files[key] = Path(path)

    try:
        storage = _storage_from_env()
    except RuntimeError as e:
        print(f"ingest capture: {e}", file=sys.stderr)
        return 2

    try:
        results = capture_bundle(
            Path(args.bundle_path), storage, source_keys=args.source, manual_files=manual_files, force=args.force,
        )
    except CaptureError as e:
        print(f"ingest capture: REFUSED -- {e}", file=sys.stderr)
        return 1

    _print_capture_results(results, args.bundle_path)

    if args.sync_db:
        try:
            conn = _connect()
        except RuntimeError as e:
            print(f"ingest capture --sync-db: {e}", file=sys.stderr)
            return 2
        bundle = load_ingestion_bundle(args.bundle_path)
        with conn:
            updated = sync_captured_sources_to_db(conn, bundle)
        print(f"--sync-db: updated {len(updated)} live sources row(s): {updated}")

    return 0


def _print_report(report: LintReport, bundle_path: str) -> None:
    print(f"ingest lint: {bundle_path}")
    print(f"checks run: {', '.join(report.checks_run)}")
    print(f"checks NOT implemented (Part C SS C.11's original battery, see docs/DECISIONS.md):")
    for check in report.checks_not_implemented:
        print(f"  - {check}")
    print()

    if not report.issues:
        print("No issues found by the checks that ARE implemented.")
    else:
        for issue in report.issues:
            print(f"[{issue.severity}] {issue.check} -- {issue.entity}: {issue.message}")

    print()
    if report.passed:
        print("PASSED (no errors from the implemented checks -- this does NOT mean the bundle "
              "is publish-ready; see the not-implemented list above and Part I SS I.4/I.8's full gate).")
    else:
        print(f"FAILED -- {len(report.errors)} error(s). Fix and re-run before LINK.")


def cmd_lint(args: argparse.Namespace) -> int:
    bundle = load_ingestion_bundle(args.bundle_path)
    report = lint_bundle(bundle)
    _print_report(report, args.bundle_path)
    return 0 if report.passed else 1


def _print_link_result(result: LinkResult, bundle_path: str) -> None:
    print(f"ingest link: {bundle_path}")
    print(f"card {result.card_key!r} linked -- card_id={result.card_id}, card_version_id={result.card_version_id}, version_no={result.version_no}")
    print(f"sources: {result.sources_inserted} inserted, {result.sources_reused} reused (deduped by URL)")
    for entity_type, count in result.entity_counts.items():
        print(f"  {entity_type}: {count}")
    print(f"source_links inserted: {result.source_links_inserted} (all reviewer_status='unreviewed')")
    print()
    if result.version_no > 1:
        print(f"card_version status='draft' (v{result.version_no}, supersedes v{result.version_no - 1} on publish) -- "
              "run `ingest review-queue` next, then a human review pass, before `ingest publish`.")
    else:
        print("card_version status='draft' -- run `ingest review-queue` next, then a human review pass, "
              "before `ingest publish`.")


def _connect():
    """Raises RuntimeError with a clear message if DATABASE_URL is unset --
    callers print it and exit 2, same convention across every DB subcommand.
    prepare_threshold=None: see app/repository.py's PostgresCardRepository /
    docs/DECISIONS.md #136 -- Supabase's pooler doesn't support psycopg3's
    auto-prepared statements."""
    import psycopg

    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg.connect(url, prepare_threshold=None)


def cmd_link(args: argparse.Namespace) -> int:
    try:
        conn = _connect()
    except RuntimeError as e:
        print(f"ingest link: {e}", file=sys.stderr)
        return 2

    bundle = load_ingestion_bundle(args.bundle_path)
    try:
        with conn:
            result = link_bundle(bundle, conn, new_version=args.new_version)
    except LinkError as e:
        print(f"ingest link: REFUSED -- {e}", file=sys.stderr)
        return 1

    _print_link_result(result, args.bundle_path)
    return 0


def _print_review_queue(groups: tuple[ReviewQueueGroup, ...]) -> None:
    if not groups:
        print("Review queue is empty -- no unreviewed source_links.")
        return
    total = sum(len(g.items) for g in groups)
    print(f"ingest review-queue: {total} unreviewed source_link(s) across {len(groups)} group(s)")
    print("(listing only -- flip reviewer_status via Supabase's Table Editor or a direct UPDATE; SS I.5 requires a human, not this tool, to do it)")
    print()
    for group in groups:
        cv_note = f", card_version_id={group.card_version_id}" if group.card_version_id else ""
        print(f"{group.label}{cv_note} ({len(group.items)}):")
        for item in group.items:
            print(f"  [{item.source_link_id}] {item.entity_type} {item.entity_key!r} -- confidence={item.confidence}, {item.source_type} <{item.source_url}>")
        print()


def cmd_review_queue(args: argparse.Namespace) -> int:
    try:
        conn = _connect()
    except RuntimeError as e:
        print(f"ingest review-queue: {e}", file=sys.stderr)
        return 2

    with conn:
        groups = build_review_queue(conn)
    _print_review_queue(groups)
    return 0


def _print_publish_result(result: PublishResult) -> None:
    print(f"ingest publish: card {result.card_key!r}, card_version_id={result.card_version_id}")
    for r in result.scenario_results:
        status = "PASS" if r.passed else "fail"
        print(f"  [{status}] {r.golden_path} :: {r.scenario_name}")
        for diff in r.diffs:
            print(f"      {diff}")
    print()
    print("PUBLISHED -- status='published', published_at=now(). This is IRREVERSIBLE from here "
          "(Part D Decision 2): only 'deprecated' or a new version can follow.")
    if result.superseded_version_id:
        print(f"Predecessor card_version {result.superseded_version_id} closed out -- its effective_to is now "
              "set to the day before this version's effective_from (Part I SS I.6 step 4).")


def cmd_publish(args: argparse.Namespace) -> int:
    try:
        conn = _connect()
    except RuntimeError as e:
        print(f"ingest publish: {e}", file=sys.stderr)
        return 2

    try:
        with conn:
            result = publish_card_version(conn, args.card_version_id, args.golden or [])
    except PublishError as e:
        print(f"ingest publish: REFUSED -- {e}", file=sys.stderr)
        return 1

    _print_publish_result(result)
    return 0


def cmd_reward_catalog_ratio(args: argparse.Namespace) -> int:
    try:
        report = refresh_reference(args.card)
    except CatalogFetchError as e:
        print(f"ingest reward-catalog-ratio: REFUSED -- {e}", file=sys.stderr)
        return 1

    print(f"ingest reward-catalog-ratio: fetched {report['source_url']} ({report['captured_at']})")
    print(f"{report['total_catalog_items_parsed']} catalog items parsed.")
    print()
    for card_key, stats in report["segments"].items():
        if isinstance(stats, str):
            print(f"  {card_key}: {stats}")
        else:
            print(
                f"  {card_key}: n={stats['n']}, median={stats['median_per_100_points']}/100pts, "
                f"mean={stats['mean_per_100_points']}/100pts, range=[{stats['min_per_100_points']}, "
                f"{stats['max_per_100_points']}]"
            )
    print()
    print("This is a DISTRIBUTION summary, not a citable fact -- needs sign-off before use in any bundle (see report['methodology']).")

    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2) + "\n")
        print(f"Written to {args.out}")

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ingest", description="Part I SS I.9 ingestion tooling.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    capture_parser = subparsers.add_parser(
        "capture", help="Run CAPTURE (SS I.1): fetch/store each source's snapshot, record storage_path/captured_at."
    )
    capture_parser.add_argument("bundle_path", help="Path to an ingestion bundle JSON file.")
    capture_parser.add_argument("--source", action="append", help="Restrict to this source key (repeatable). Default: all declared sources.")
    capture_parser.add_argument("--file", action="append", help="KEY=path -- use an already-downloaded file for source KEY instead of fetching (repeatable).")
    capture_parser.add_argument("--force", action="store_true", help="Re-capture sources that already have storage_path/captured_at.")
    capture_parser.add_argument("--sync-db", action="store_true", help="Also push storage_path/captured_at into the live sources row for an already-linked card (matched by URL).")
    capture_parser.set_defaults(func=cmd_capture)

    lint_parser = subparsers.add_parser("lint", help="Run LINT (SS I.4) on a bundle file -- no database access.")
    lint_parser.add_argument("bundle_path", help="Path to an ingestion bundle JSON file.")
    lint_parser.set_defaults(func=cmd_lint)

    link_parser = subparsers.add_parser(
        "link", help="Run LINT then LINK (SS I.4): inserts the card/sources/source_links as status='draft'."
    )
    link_parser.add_argument("bundle_path", help="Path to an ingestion bundle JSON file.")
    link_parser.add_argument(
        "--new-version", action="store_true",
        help="Devaluation flow (SS I.6): supersede this card's latest PUBLISHED version with a new draft "
             "version_no+1, instead of refusing because the card already exists.",
    )
    link_parser.set_defaults(func=cmd_link)

    review_parser = subparsers.add_parser(
        "review-queue", help="List unreviewed source_links (SS I.4 REVIEW), grouped by card. Read-only."
    )
    review_parser.set_defaults(func=cmd_review_queue)

    publish_parser = subparsers.add_parser(
        "publish", help="Check SS I.8's full gate and flip a card_version to status='published' (IRREVERSIBLE)."
    )
    publish_parser.add_argument("card_version_id", help="UUID of the card_versions row to publish.")
    publish_parser.add_argument(
        "--golden", action="append",
        help="Path to a hand-computed golden JSON (repeatable). At least one scenario across all given "
             "files must pass evaluate_card exactly (SS I.8) -- required.",
    )
    publish_parser.set_defaults(func=cmd_publish)

    ratio_parser = subparsers.add_parser(
        "reward-catalog-ratio",
        help="DRAFT-time helper (not a pipeline stage): estimate a points-to-rupee ratio from the live "
             "sbicard.com rewards catalog for one or more card segments, when no T&C states a fixed one.",
    )
    ratio_parser.add_argument("--card", action="append", required=True, help="Card key to report on (repeatable), e.g. sbi-card-prime.")
    ratio_parser.add_argument("--out", help="Optional path to write the full JSON report to.")
    ratio_parser.set_defaults(func=cmd_reward_catalog_ratio)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
