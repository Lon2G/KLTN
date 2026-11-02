"""Formatting shared by the historical data and manual-review audits."""

import pandas as pd


def format_value(value) -> str:
    """Format scalar report values without unnecessary float suffixes."""
    if pd.isna(value):
        return ""
    numeric_value = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.notna(numeric_value):
        if float(numeric_value).is_integer():
            return str(int(numeric_value))
        return str(numeric_value)
    return str(value)
