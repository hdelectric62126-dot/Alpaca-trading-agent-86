"""Generate PAPER performance reports without contacting Alpaca."""

import argparse
import json

from journal import journal_path
from performance_agent import PerformanceAgent


def main():
    parser = argparse.ArgumentParser(description="Generate a PAPER trading journal report")
    parser.add_argument("--days", type=int, choices=(1, 7, 30), required=True)
    parser.add_argument("--db", help="Override JOURNAL_DB_PATH")
    args = parser.parse_args()
    result = PerformanceAgent(args.db or journal_path()).analyze(args.days)
    print(f"PAPER performance report ({args.days} day{'s' if args.days != 1 else ''})")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
