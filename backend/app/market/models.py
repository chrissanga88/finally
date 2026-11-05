"""Data models for market data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Direction = Literal["up", "down", "flat"]


@dataclass(frozen=True, slots=True)
class PriceUpdate:
    """Latest price for one ticker. Created by PriceCache.update()."""

    ticker: str
    price: float  # rounded to 2 dp
    previous_price: float  # price at the previous update (tick-over-tick)
    timestamp: float  # Unix seconds (float)
    session_open: float  # reference price for session change % (see PriceCache)

    @property
    def change(self) -> float:
        """Absolute tick-over-tick change."""
        return round(self.price - self.previous_price, 4)

    @property
    def change_percent(self) -> float:
        """Tick-over-tick change in percent. Not for display (PLAN §6)."""
        if self.previous_price == 0:
            return 0.0
        return round((self.price - self.previous_price) / self.previous_price * 100, 4)

    @property
    def direction(self) -> Direction:
        if self.price > self.previous_price:
            return "up"
        if self.price < self.previous_price:
            return "down"
        return "flat"

    @property
    def session_change_percent(self) -> float:
        """Change since session_open, in percent (0.65 == 0.65%)."""
        if self.session_open == 0:
            return 0.0
        return round((self.price - self.session_open) / self.session_open * 100, 4)

    def to_dict(self) -> dict[str, str | float]:
        """Serialize for SSE / JSON. Keys and units match PLAN §6."""
        return {
            "ticker": self.ticker,
            "price": self.price,
            "previous_price": self.previous_price,
            "timestamp": self.timestamp,
            "change": self.change,
            "change_percent": self.change_percent,
            "direction": self.direction,
            "session_open": self.session_open,
            "session_change_percent": self.session_change_percent,
        }
