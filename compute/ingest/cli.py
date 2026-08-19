"""`ingest` CLI (Part I SS I.9).

Only `lint` is a real subcommand this pass -- `link`/`review-queue`/
`publish` touch Postgres (SS I.4's LINK/REVIEW/PUBLISH stages) and are
deliberately not built yet, per the project's own incremental-with-
verification discipline (each Phase 2-4 slice was one command/module at
a time, tested before the next). Registering stub subcommands that print
"not implemented" would look like partial coverage of something that
doesn't exist yet; leaving them unregistered and saying so in this
module's own docstring is the more honest signal.

Usage:
    python -m ingest lint compute/ingestion/bundle_sbi_cashback.json
"""
from __future__ import annotations

import argparse
import sys

from ingest.bundle import load_ingestion_bundle
from ingest.lint import LintReport, lint_bundle


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ingest", description="Part I SS I.9 ingestion tooling.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    lint_parser = subparsers.add_parser("lint", help="Run LINT (SS I.4) on a bundle file -- no database access.")
    lint_parser.add_argument("bundle_path", help="Path to an ingestion bundle JSON file.")
    lint_parser.set_defaults(func=cmd_lint)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
