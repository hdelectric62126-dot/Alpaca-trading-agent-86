"""Fetch daily history and run the walk-forward paper strategy simulation."""

import argparse
import os
from datetime import datetime, timedelta, timezone

from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from strategy import walk_forward_backtest


def load_history(symbol: str, days: int):
    client = StockHistoricalDataClient(os.environ["APCA_API_KEY_ID"], os.environ["APCA_API_SECRET_KEY"])
    request = StockBarsRequest(
        symbol_or_symbols=[symbol],
        timeframe=TimeFrame.Day,
        start=datetime.now(timezone.utc) - timedelta(days=days),
        end=datetime.now(timezone.utc),
        feed=DataFeed.IEX,
    )
    bars = client.get_stock_bars(request).df
    return bars.xs(symbol) if hasattr(bars.index, "levels") else bars


def main():
    parser = argparse.ArgumentParser(description="Run a walk-forward backtest for Alpaca symbols.")
    parser.add_argument("--symbols", default="AMD,TSM,TSLA")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--minimum-score", type=int, default=60)
    args = parser.parse_args()
    for symbol in (value.strip().upper() for value in args.symbols.split(",")):
        bars = load_history(symbol, args.days)
        result = walk_forward_backtest(bars, minimum_score=args.minimum_score)
        print(f"{symbol}: {result}")


if __name__ == "__main__":
    main()
