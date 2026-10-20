from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import math
import random
from io import StringIO
from typing import Iterable

from .domain import Candle, parse_datetime


UTC = timezone.utc
BASE_PRICES = {
    "EUR/USD": 1.0850,
    "GBP/USD": 1.2740,
    "USD/JPY": 149.50,
    "AUD/USD": 0.6620,
    "USD/INR": 83.50,
}


def validate_candles(candles: Iterable[Candle], minimum: int = 1) -> list[Candle]:
    result = list(candles)
    if len(result) < minimum:
        raise ValueError(f"At least {minimum} candles are required")
    previous = None
    for candle in result:
        if previous is not None and candle.timestamp <= previous:
            raise ValueError("Candle timestamps must be unique and in ascending order")
        previous = candle.timestamp
    return result


def parse_candles_csv(text: str, minimum: int = 30) -> list[Candle]:
    """Read timestamp,open,high,low,close[,volume] CSV data."""
    reader = csv.DictReader(StringIO(text.lstrip("\ufeff")))
    if reader.fieldnames is None:
        raise ValueError("CSV is empty or has no header row")
    columns = {name.strip().lower() for name in reader.fieldnames if name}
    if not {"open", "high", "low", "close"}.issubset(columns):
        raise ValueError("CSV needs open, high, low, and close columns")
    if not ({"timestamp", "time", "date"} & columns):
        raise ValueError("CSV needs a timestamp, time, or date column")

    rows: list[Candle] = []
    for line_number, raw in enumerate(reader, start=2):
        row = {(key or "").strip().lower(): value for key, value in raw.items()}
        if not any(value not in (None, "") for value in row.values()):
            continue
        try:
            rows.append(Candle.from_mapping(row))
        except ValueError as exc:
            raise ValueError(f"CSV line {line_number}: {exc}") from exc
    return validate_candles(rows, minimum=minimum)


def build_demo_candles(
    pair: str = "EUR/USD",
    count: int = 720,
    now: datetime | None = None,
) -> list[Candle]:
    """Create clearly synthetic, repeatable one-minute candles for the demo UI."""
    if count < 40:
        raise ValueError("Demo history must contain at least 40 candles")
    pair = pair.upper()
    base = BASE_PRICES.get(pair, 1.0)
    seed = sum((index + 1) * ord(char) for index, char in enumerate(pair))
    rng = random.Random(seed)
    current_minute = (now or datetime.now(UTC)).astimezone(UTC).replace(second=0, microsecond=0)
    last_open = current_minute - timedelta(minutes=1)
    start = last_open - timedelta(minutes=count - 1)

    price = base
    candles: list[Candle] = []
    for index in range(count):
        # A deliberately artificial mix of drift, cycles, and noise. It is not market data.
        cycle = 0.00010 * math.sin(index / 29.0) + 0.000045 * math.sin(index / 8.0)
        regime = 0.000055 if (index // 105) % 2 == 0 else -0.000045
        change = cycle + regime + rng.gauss(0.0, 0.00018)
        open_price = price
        close_price = max(base * 0.5, open_price * (1.0 + change))
        wick = abs(rng.gauss(0.00007, 0.000035))
        high_price = max(open_price, close_price) * (1.0 + wick)
        low_price = min(open_price, close_price) * (1.0 - wick)
        timestamp = start + timedelta(minutes=index)
        candles.append(
            Candle(
                timestamp=timestamp,
                open=open_price,
                high=high_price,
                low=low_price,
                close=close_price,
                volume=float(rng.randint(80, 420)),
            )
        )
        price = close_price
    return candles
