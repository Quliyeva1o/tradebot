"""Data infrastructure package.

Exposes concrete data providers such as CSVDataProvider.
"""

from data.csv_provider import CSVDataProvider

__all__ = ["CSVDataProvider"]
