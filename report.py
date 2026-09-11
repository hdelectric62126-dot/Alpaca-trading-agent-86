"""Generate PAPER performance reports without contacting Alpaca."""

import argparse
import json

from journal import PerformanceAnalyzer, journal_path


def main():
    parser = argparse.ArgumentParser(description="Generate a PAPER trading journal report")
    parser.add_argument("--days", type=int, choices=(1, 7, 30), required=True)
    parser.add_argument("--db", help="Override JOURNAL_DB_PATH")
    args = parser.parse_args()
    report = PerformanceAnalyzer(args.db or journal_path()).report(args.days)
    print(f"PAPER performance report ({args.days} day{'s' if args.days != 1 else ''})")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()