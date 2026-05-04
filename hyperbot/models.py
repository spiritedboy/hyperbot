from dataclasses import dataclass
from enum import Enum
from typing import Optional


class PositionAction(str, Enum):
    OPEN = "open"
    ADD = "add"
    REDUCE = "reduce"
    CLOSE = "close"
    FLIP = "flip"


@dataclass
class PositionSnapshot:
    coin: str
    size: float
    notional_usd: float
    leverage: float
    margin_mode: str
    account_value: float
    unrealized_pnl_usd: Optional[float] = None
    liquidation_price: Optional[float] = None
    entry_price: Optional[float] = None

    @property
    def direction(self) -> str:
        if self.size > 0:
            return "LONG"
        if self.size < 0:
            return "SHORT"
        return "FLAT"

    @property
    def principal_usd(self) -> float:
        if self.leverage <= 0:
            return 0.0
        return abs(self.notional_usd) / self.leverage

    @property
    def principal_ratio(self) -> float:
        if self.account_value <= 0:
            return 0.0
        return self.principal_usd / self.account_value
