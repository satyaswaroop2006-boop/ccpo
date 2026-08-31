"""`ingest` CLI (Part I SS I.9). All four subcommands SS I.9 specifies are
now built: `lint`, `link`, `review-queue`, `publish`.

`review-queue` is read-only -- it never flips `reviewer_status`. That
flip is a human act SS I.5 requires stay outside any automated tool
("never set by Claude, never set by the same automated step that drafted
the field"); Supabase's own Table Editor already lets a non-technical
reviewer do it with a dropdown, so this CLI doesn't need its own
mutation command to make the workflow usable.

Usage:
    python -m ingest lint compute/ingestion/bundle_sbi_cashback.json
    DATABASE_URL=postgresql://... python -m ingest link compute/ingestion/bundle_sbi_cashback.json
    DATABASE_URL=postgresql://... python -m ingest review-queue
    DATABASE_URL=postgresql://... python -m ingest publish <card_version_id> --golden compute/ingestion/golden_sbi_cashback.json
"""
from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

from ingest.bundle import load_ingestion_bundle
from ingest.lint import LintReport, lint_bundle
from ingest.link import LinkError, LinkResult, link_bundle
from ingest.publish import PublishError, PublishResult, publish_card_version
from ingest.review import ReviewQueueGroup, build_review_queue

load_dotenv()  # compute/.env's DATABASE_URL, local-dev convenience -- same as app/main.py


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
    print(f"card {result.card_key!r} linked -- card_id={result.card_id}, card_version_id={result.card_version_id}")
    print(f"sources: {result.sources_inserted} inserted, {result.sources_reused} reused (deduped by URL)")
    for entity_type, count in result.entity_counts.items():
        print(f"  {entity_type}: {count}")
    print(f"source_links inserted: {result.source_links_inserted} (all reviewer_status='unreviewed')")
    print()
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
            result = link_bundle(bundle, conn)
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ingest", description="Part I SS I.9 ingestion tooling.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    lint_parser = subparsers.add_parser("lint", help="Run LINT (SS I.4) on a bundle file -- no database access.")
    lint_parser.add_argument("bundle_path", help="Path to an ingestion bundle JSON file.")
    lint_parser.set_defaults(func=cmd_lint)

    link_parser = subparsers.add_parser(
        "link", help="Run LINT then LINK (SS I.4): inserts the card/sources/source_links as status='draft'."
    )
    link_parser.add_argument("bundle_path", help="Path to an ingestion bundle JSON file.")
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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
