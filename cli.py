"""CLI entry point. Original scripts in Spira_api_info remain untouched."""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from client import Settings, SpiraClient, SpiraError
from report import build_report, export_report


def parser():
    root = argparse.ArgumentParser(description="Read-only DH8 Spira report generator")
    root.add_argument("--config", default=str(Path(__file__).with_name("spira.ini")))
    root.add_argument("--diagnostic-errors", action="store_true",
                      help="Show bounded, credential-redacted server error messages")
    commands = root.add_subparsers(dest="command", required=True)
    discover = commands.add_parser("discover", help="List selection IDs as JSON")
    discover.add_argument("kind", choices=["projects", "releases", "folders", "test-sets"])
    extract = commands.add_parser("report", help="Extract selected data and generate a report")
    extract.add_argument("--release", type=int, required=True)
    selection = extract.add_mutually_exclusive_group(required=True)
    selection.add_argument("--test-set", type=int, nargs="+")
    selection.add_argument("--folder", type=int)
    extract.add_argument("--output", required=True)
    extract.add_argument("--snapshot", help="Save raw extraction for reproducible offline reporting")
    offline = commands.add_parser("offline", help="Generate report from an extraction snapshot")
    offline.add_argument("--snapshot", required=True)
    offline.add_argument("--output", required=True)
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "offline":
            if Path(args.snapshot).resolve() == Path(args.output).resolve():
                raise SpiraError("Snapshot and report must use different paths.")
            data = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
        else:
            client = SpiraClient(Settings.load(args.config), diagnostic_errors=args.diagnostic_errors)
            if args.command == "discover":
                operation = {"projects": client.projects,
                             "releases": client.releases, "folders": client.folders,
                             "test-sets": client.test_sets}[args.kind]
                print(json.dumps(operation(), indent=2, ensure_ascii=False))
                return 0
            if args.release <= 0 or (args.folder is not None and args.folder <= 0) or (
                    args.test_set and any(tx <= 0 for tx in args.test_set)):
                raise SpiraError("Selection IDs must be positive integers.")
            data = client.extract(args.release, args.test_set or (), args.folder)
            data["extracted_utc"] = datetime.now(timezone.utc).isoformat()
        report = build_report(data)
        if args.command == "report" and args.snapshot:
            if Path(args.snapshot).resolve() == Path(args.output).resolve():
                raise SpiraError("Snapshot and report must use different output paths.")
            Path(args.snapshot).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        export_report(report, args.output)
        print(json.dumps(report["summary"], indent=2))
        for warning in report["metadata"]["warnings"]:
            print("WARNING: " + warning, file=sys.stderr)
        print(f"Report written: {args.output}")
        return 0
    except (SpiraError, OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
