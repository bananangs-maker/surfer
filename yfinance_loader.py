"""yfinance adapter. Quarantined by construction - the flag is not optional.

Yahoo serves at most 730 days of 60-minute bars, so this source cannot reach
2018 Q4, Feb-Mar 2020, or 2022. Those are precisely the windows that decide
whether a leveraged-ETF swing system is viable. What remains is a window in
which the underlying index mostly rose.

That makes this data useful for exactly two things: proving the pipeline runs,
and measuring the structural ambiguity rate (which does not depend on regime).
Both are honest uses. Reporting a return figure from it is not, which is why
Dataset.quarantined is set to True here with no parameter to turn it off.
"""

from __future__ import annotations

import pandas as pd

from ..integrity import annotate_sessions
from ..schema import BAR_COLUMNS, Adjustment, Dataset

MAX_LOOKBACK_DAYS = 730


def load_yfinance(
    symbol: str,
    period: str = "730d",
    interval: str = "60m",
    prepost: bool = False,
) -> Dataset:
    try:
        import yfinance as yf
    except ImportError as e:  # pragma: no cover
        raise ImportError("pip install yfinance") from e

    raw = yf.Ticker(symbol).history(
        period=period, interval=interval, prepost=prepost, auto_adjust=True
    )
    if raw.empty:
        raise ValueError(
            f"no data for {symbol}. Yahoo caps {interval} history at "
            f"{MAX_LOOKBACK_DAYS} days; a longer period silently returns empty."
        )

    df = raw.reset_index()
    tscol = "Datetime" if "Datetime" in df.columns else df.columns[0]
    df = df.rename(
        columns={
            tscol: "ts",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df[["ts", "open", "high", "low", "close", "volume"]]
    df = annotate_sessions(df, interval_minutes=60)

    return Dataset(
        bars=df[BAR_COLUMNS],
        symbol=symbol,
        source="yfinance",
        interval_minutes=60,
        # auto_adjust=True adjusts for splits and dividends, but Yahoo's
        # intraday adjustment is not consistently applied across split
        # boundaries. integrity.check() flags candidates; do not trust silently.
        adjustment=Adjustment.SPLIT_AND_DIVIDEND,
        quarantined=True,
        quarantine_reason=(
            f"Yahoo 60m history is capped at {MAX_LOOKBACK_DAYS} days; window "
            "excludes 2018Q4 / 2020Q1 / 2022. Plumbing and structural "
            "diagnostics only - no performance claims."
        ),
    )
