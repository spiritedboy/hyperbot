from __future__ import annotations

import logging
import time
from decimal import Decimal, ROUND_DOWN
from typing import Callable, Dict, List, Optional, TypeVar

from eth_account import Account

from hyperliquid.exchange import Exchange
from hyperliquid.info import Info

from hyperbot.models import PositionSnapshot


T = TypeVar("T")


class HyperliquidClient:
    STABLE_COINS = {"USDC", "USDT0", "USDE", "USDH", "USDT"}

    def __init__(self, api_url: str, private_key: Optional[str] = None):
        # 先用基础 Info 获取所有 perp DEX（包含 HIP-3 等 builder-deployed DEX）
        _boot = Info(api_url, skip_ws=True)
        try:
            raw_dexs = _boot.perp_dexs()
            self._extra_dexs: List[str] = [
                d["name"] for d in raw_dexs[1:] if isinstance(d, dict) and d.get("name")
            ]
        except Exception as exc:
            logging.warning("无法获取 perp DEXs 列表，仅使用主 DEX: %s", exc)
            self._extra_dexs = []

        all_dex_names = [""] + self._extra_dexs
        self.info = Info(
            api_url,
            skip_ws=True,
            perp_dexs=all_dex_names if self._extra_dexs else None,
        )
        self.exchange: Optional[Exchange] = None
        self._request_retries = 3
        self._request_retry_delay = 0.35
        if private_key:
            wallet = Account.from_key(private_key)
            self.exchange = Exchange(wallet, api_url)
            # 让 exchange 的内部 info 也感知所有 DEX，以便下单时 name_to_asset 能查到 HIP-3 币种
            self.exchange.info = self.info

    @property
    def extra_dexs(self) -> List[str]:
        return list(self._extra_dexs)

    def get_account_value(self, address: str) -> float:
        _, _, total_value = self.get_account_values(address)
        return total_value

    def get_account_values(self, address: str) -> tuple[float, float, float]:
        perp_value = self.get_perp_account_value(address)
        spot_value = self.get_spot_account_value(address)
        return perp_value, spot_value, perp_value + spot_value

    def get_perp_account_value(self, address: str) -> float:
        state = self._with_retry("user_state", lambda: self.info.user_state(address))
        margin = state.get("marginSummary", {})
        return float(margin.get("accountValue", 0.0))

    def get_spot_account_value(self, address: str) -> float:
        try:
            state = self._with_retry(
                "spot_clearinghouse_state",
                lambda: self.info.post("/info", {"type": "spotClearinghouseState", "user": address}),
            )
        except Exception:
            return 0.0

        balances = state.get("balances", []) if isinstance(state, dict) else []
        total = 0.0
        for bal in balances:
            coin = str(bal.get("coin", "")).upper()
            if coin not in self.STABLE_COINS:
                continue
            total += float(bal.get("total", 0.0))
        return total

    def get_positions(self, address: str) -> Dict[str, PositionSnapshot]:
        _, _, account_value = self.get_account_values(address)
        # 主 DEX 仓位
        state = self._with_retry("user_state", lambda: self.info.user_state(address))
        positions = self._parse_positions_from_state(state, account_value)
        # 额外 DEX 仓位（HIP-3 等）
        for dex in self._extra_dexs:
            try:
                dex_state = self._with_retry(
                    f"user_state_{dex}",
                    lambda d=dex: self.info.user_state(address, dex=d),
                )
                positions.update(self._parse_positions_from_state(dex_state, account_value))
            except Exception as exc:
                logging.warning("获取 dex=%s 仓位失败: %s", dex, exc)
        return positions

    def _parse_positions_from_state(self, state: Dict, account_value: float) -> Dict[str, PositionSnapshot]:
        positions: Dict[str, PositionSnapshot] = {}
        for item in state.get("assetPositions", []):
            pos = item.get("position", {})
            coin = pos.get("coin")
            if not coin:
                continue
            size = float(pos.get("szi", 0.0))
            if size == 0:
                continue
            notional = abs(float(pos.get("positionValue", 0.0)))
            if notional == 0:
                entry_px = float(pos.get("entryPx", 0.0))
                notional = abs(size * entry_px)
            leverage_raw = pos.get("leverage", {})
            leverage = float(leverage_raw.get("value", 1.0)) if isinstance(leverage_raw, dict) else float(leverage_raw)
            margin_mode = "cross"
            if isinstance(leverage_raw, dict):
                if leverage_raw.get("type"):
                    margin_mode = str(leverage_raw["type"])
            if pos.get("marginMode"):
                margin_mode = str(pos["marginMode"])
            if pos.get("marginType"):
                margin_mode = str(pos["marginType"])
            unrealized_pnl = self._first_float(pos, ["unrealizedPnl", "unrealizedPnlUsd", "uPnl", "upnl"])
            liquidation_price = self._first_float(pos, ["liquidationPx", "liquidationPrice", "liqPx"])
            positions[coin] = PositionSnapshot(
                coin=coin,
                size=size,
                notional_usd=notional,
                leverage=leverage,
                margin_mode=margin_mode,
                account_value=account_value,
                unrealized_pnl_usd=unrealized_pnl,
                liquidation_price=liquidation_price,
            )
        return positions

    def get_mid_price(self, coin: str) -> float:
        mids = self._with_retry("all_mids", self.info.all_mids)
        if coin in mids:
            return float(mids[coin])
        # 主 DEX 未找到时，依次尝试额外 DEX（HIP-3 等）
        for dex in self._extra_dexs:
            try:
                dex_mids = self._with_retry(f"all_mids_{dex}", lambda d=dex: self.info.all_mids(dex=d))
                if coin in dex_mids:
                    return float(dex_mids[coin])
            except Exception as exc:
                logging.warning("获取 dex=%s 价格失败: %s", dex, exc)
        raise ValueError(f"未找到 {coin} 的中间价")

    def configure_leverage_and_mode(self, coin: str, leverage: float, margin_mode: str, dry_run: bool) -> None:
        if dry_run:
            return
        if self.exchange is None:
            raise RuntimeError("当前客户端未初始化交易权限")

        is_cross = margin_mode.lower() == "cross"
        self.exchange.update_leverage(int(round(leverage)), coin, is_cross)

    def market_order(
        self,
        coin: str,
        is_buy: bool,
        notional_usd: float,
        slippage: float,
        reduce_only: bool,
        dry_run: bool,
    ) -> Dict:
        px = self.get_mid_price(coin)
        if px <= 0:
            raise ValueError(f"{coin} 价格异常")

        sz = notional_usd / px
        sz = self._normalize_size(coin, sz)
        if sz <= 0:
            raise ValueError("下单数量不能为0")

        limit_px = self._normalize_price(coin, px * (1 + slippage if is_buy else 1 - slippage))

        if dry_run:
            return {
                "status": "ok",
                "response": {
                    "dryRun": True,
                    "coin": coin,
                    "is_buy": is_buy,
                    "notional_usd": notional_usd,
                    "size": sz,
                    "limit_px": limit_px,
                    "reduce_only": reduce_only,
                },
            }

        if self.exchange is None:
            raise RuntimeError("当前客户端未初始化交易权限")

        order_type = {"limit": {"tif": "Ioc"}}
        return self.exchange.order(coin, is_buy, sz, limit_px, order_type, reduce_only=reduce_only)

    def close_position_market(self, coin: str, size: float, slippage: float, dry_run: bool) -> Dict:
        if size == 0:
            return {"status": "ok", "response": {"skipped": True}}

        notional = abs(size) * self.get_mid_price(coin)
        is_buy = size < 0
        return self.market_order(
            coin=coin,
            is_buy=is_buy,
            notional_usd=notional,
            slippage=slippage,
            reduce_only=True,
            dry_run=dry_run,
        )

    def estimate_recent_closed_pnl(self, address: str, lookback_seconds: int = 180) -> Optional[float]:
        end_ms = int(time.time() * 1000)
        start_ms = end_ms - lookback_seconds * 1000
        fills: List[Dict] = self._with_retry(
            "user_fills_by_time",
            lambda: self.info.user_fills_by_time(address, start_ms, end_ms),
        )

        total = 0.0
        found = False
        for fill in fills:
            if "closedPnl" in fill:
                total += float(fill["closedPnl"])
                found = True

        return total if found else None

    @staticmethod
    def _first_float(payload: Dict, keys: List[str]) -> Optional[float]:
        for key in keys:
            if key not in payload:
                continue
            value = payload.get(key)
            if value is None:
                continue
            if isinstance(value, str) and value.strip() == "":
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return None

    def _normalize_size(self, coin: str, size: float) -> float:
        asset = self.info.name_to_asset(coin)
        sz_decimals = self.info.asset_to_sz_decimals[asset]
        quantum = Decimal("1").scaleb(-sz_decimals)
        normalized = Decimal(str(size)).quantize(quantum, rounding=ROUND_DOWN)
        return float(normalized)

    def _normalize_price(self, coin: str, price: float) -> float:
        asset = self.info.name_to_asset(coin)
        sz_decimals = self.info.asset_to_sz_decimals[asset]
        max_decimals = 6

        if price > 100_000:
            return float(round(price))

        rounded_sig = float(f"{price:.5g}")
        decimals = max(max_decimals - sz_decimals, 0)
        normalized = round(rounded_sig, decimals)
        return float(normalized)

    def _with_retry(self, op_name: str, fn: Callable[[], T]) -> T:
        last_error: Optional[Exception] = None
        for attempt in range(1, self._request_retries + 1):
            try:
                return fn()
            except Exception as exc:
                last_error = exc
                if attempt >= self._request_retries:
                    break
                sleep_seconds = self._request_retry_delay * attempt
                logging.warning(
                    "Hyperliquid API 调用失败，准备重试 op=%s attempt=%s/%s error=%s",
                    op_name,
                    attempt,
                    self._request_retries,
                    exc,
                )
                time.sleep(sleep_seconds)

        assert last_error is not None
        raise last_error
