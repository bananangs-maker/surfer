"""Loaders are adapters. The engine knows only schema.Dataset.

Swapping the free 730-day source for purchased history must be a change of one
call here and nothing else. If a future change to the data source requires
edits in fills.py or levels.py, the contract in schema.py has been broken and
that is the bug to fix.
"""

from .firstrate_csv import load_firstrate_csv
from .synthetic import load_synthetic
from .yfinance_loader import load_yfinance

__all__ = ["load_firstrate_csv", "load_synthetic", "load_yfinance"]
