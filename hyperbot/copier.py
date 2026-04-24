from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Dict, Optional

from hyperbot.config import Settings
from hyperbot.feishu import FeishuNotifier
from hyperbot.hyperliquid_client import HyperliquidClient
from hyperbot.models import PositionAction, PositionSnapshot


@dataclass
class ActionEvent:
    action: PositionAction
    previous: Optional[PositionSnapshot]
    current: Optional[PositionSnapshot]


class CopyTradingEngine:
    def __init__(
        self,
        settings: Settings,
        leader_client: HyperliquidClient,
        follower_client: HyperliquidClient,
        notifier: FeishuNotifier,
    ):
        self.settings = settings
        self.leader_client = leader_client
        self.follower_client = follower_client
        self.notifier = notifier
        self._last_leader_positions: Dict[str, PositionSnapshot] = {}
        self._last_event_ts_by_coin: Dict[str, float] = {}
        self._event_fingerprint_ts: Dict[str, float] = {}

    def bootstrap(self) -> None:
        self._last_leader_positions = self.leader_client.get_positions(self.settings.leader_address)

    def tick(self) -> None:
        current = self.leader_client.get_positions(self.settings.leader_address)
        events = self._detect_events(self._last_leader_positions, current)
        now = time.monotonic()

        for coin, event in events.items():
            try:
                if self._should_skip_event(coin, event, now):
                    continue

                if event.current is not None:
                    self.notifier.send_leader_signal(event.action, event.current)
                elif event.previous is not None:
                    self.notifier.send_leader_signal(event.action, event.previous)

                self._handle_copy_for_event(coin, event)
            except Exception as exc:
                logging.exception("事件处理失败 coin=%s action=%s error=%s", coin, event.action.value, exc)
                self.notifier.send_text(
                    f"coin={coin} action={event.action.value} error={exc}",
                    title="单币种跟单异常",
                )

        self._last_leader_positions = current

    def build_runtime_status_text(self) -> str:
        leader_positions = self.leader_client.get_positions(self.settings.leader_address)
        follower_positions = self.follower_client.get_positions(self.settings.follower_address)
        leader_value = self.leader_client.get_account_value(self.settings.leader_address)
        follower_value = self.follower_client.get_account_value(self.settings.follower_address)
        follower_principal = self._sum_principal(follower_positions)

        return (
            f"leader账户净值: {leader_value:.4f} U\n"
            f"leader持仓币种数: {len(leader_positions)}\n"
            f"follower账户净值: {follower_value:.4f} U\n"
            f"follower持仓币种数: {len(follower_positions)}\n"
            f"follower已用本金(估算): {follower_principal:.4f} U\n"
            f"模式: {'DRY_RUN' if self.settings.dry_run else 'LIVE'}"
        )

    def _should_skip_event(self, coin: str, event: ActionEvent, now: float) -> bool:
        debounce = self.settings.event_debounce_seconds
        if debounce > 0:
            last_ts = self._last_event_ts_by_coin.get(coin)
            if last_ts is not None and now - last_ts < debounce:
                logging.info("事件去抖: coin=%s action=%s", coin, event.action.value)
                return True

        fp = self._build_event_fingerprint(coin, event)
        ttl = self.settings.duplicate_ttl_seconds
        if ttl > 0:
            old_ts = self._event_fingerprint_ts.get(fp)
            if old_ts is not None and now - old_ts < ttl:
                logging.info("事件去重: coin=%s action=%s", coin, event.action.value)
                return True

        self._last_event_ts_by_coin[coin] = now
        self._event_fingerprint_ts[fp] = now
        return False

    @staticmethod
    def _build_event_fingerprint(coin: str, event: ActionEvent) -> str:
        prev_size = 0.0 if event.previous is None else round(event.previous.size, 8)
        curr_size = 0.0 if event.current is None else round(event.current.size, 8)
        return f"{coin}|{event.action.value}|{prev_size}|{curr_size}"

    def _detect_events(
        self,
        prev_positions: Dict[str, PositionSnapshot],
        curr_positions: Dict[str, PositionSnapshot],
    ) -> Dict[str, ActionEvent]:
        coins = sorted(set(prev_positions.keys()) | set(curr_positions.keys()))
        events: Dict[str, ActionEvent] = {}

        for coin in coins:
            prev = prev_positions.get(coin)
            curr = curr_positions.get(coin)

            if prev is None and curr is not None:
                events[coin] = ActionEvent(PositionAction.OPEN, prev, curr)
                continue
            if prev is not None and curr is None:
                events[coin] = ActionEvent(PositionAction.CLOSE, prev, curr)
                continue
            if prev is None or curr is None:
                continue

            prev_sign = 1 if prev.size > 0 else -1
            curr_sign = 1 if curr.size > 0 else -1
            prev_abs = abs(prev.size)
            curr_abs = abs(curr.size)

            if prev_sign != curr_sign:
                events[coin] = ActionEvent(PositionAction.FLIP, prev, curr)
            elif curr_abs > prev_abs:
                events[coin] = ActionEvent(PositionAction.ADD, prev, curr)
            elif curr_abs < prev_abs:
                events[coin] = ActionEvent(PositionAction.REDUCE, prev, curr)

        return events

    def _handle_copy_for_event(self, coin: str, event: ActionEvent) -> None:
        if event.action == PositionAction.CLOSE:
            self._copy_close(coin)
            return

        if event.action == PositionAction.FLIP:
            self._copy_close(coin)
            if event.current is not None:
                self._copy_open_like_leader(event.current, PositionAction.OPEN)
            return

        if event.current is None:
            return

        if event.action == PositionAction.OPEN:
            self._copy_open_like_leader(event.current, PositionAction.OPEN)
        elif event.action == PositionAction.ADD:
            self._copy_open_like_leader(event.current, PositionAction.ADD)
        elif event.action == PositionAction.REDUCE:
            self._copy_reduce(event.current)

    def _copy_open_like_leader(self, leader_pos: PositionSnapshot, action: PositionAction) -> None:
        leverage = leader_pos.leverage
        margin_mode = leader_pos.margin_mode
        principal = self.settings.fixed_margin_usd
        notional = principal * leverage

        if notional < self.settings.min_notional_usd:
            self.notifier.send_text(
                f"coin={leader_pos.coin} action={action.value} notional={notional:.4f}U < min={self.settings.min_notional_usd:.4f}U",
                title="风控拦截",
            )
            return

        is_buy = leader_pos.size > 0
        if not self.settings.allow_short and not is_buy:
            self.notifier.send_text(
                f"coin={leader_pos.coin} action={action.value} 触发空单, 但 ALLOW_SHORT=false",
                title="风控拦截",
            )
            return

        if not self._check_exposure_limits(leader_pos.coin, principal):
            return

        direction = "LONG" if is_buy else "SHORT"

        self.follower_client.configure_leverage_and_mode(
            coin=leader_pos.coin,
            leverage=leverage,
            margin_mode=margin_mode,
            dry_run=self.settings.dry_run,
        )
        self.follower_client.market_order(
            coin=leader_pos.coin,
            is_buy=is_buy,
            notional_usd=notional,
            slippage=self.settings.market_slippage,
            reduce_only=False,
            dry_run=self.settings.dry_run,
        )
        self.notifier.send_follower_result(
            action=action,
            coin=leader_pos.coin,
            direction=direction,
            margin_mode=margin_mode,
            leverage=leverage,
            principal_usd=principal,
            executed_notional_usd=notional,
            dry_run=self.settings.dry_run,
        )

    def _copy_reduce(self, leader_pos: PositionSnapshot) -> None:
        follower_positions = self.follower_client.get_positions(self.settings.follower_address)
        follower_pos = follower_positions.get(leader_pos.coin)
        if follower_pos is None:
            return

        principal = self.settings.fixed_margin_usd
        reduce_notional = principal * max(leader_pos.leverage, 1)
        reduce_notional = min(reduce_notional, abs(follower_pos.notional_usd))
        if reduce_notional <= 0 or reduce_notional < self.settings.min_notional_usd:
            return

        is_buy = follower_pos.size < 0
        self.follower_client.market_order(
            coin=leader_pos.coin,
            is_buy=is_buy,
            notional_usd=reduce_notional,
            slippage=self.settings.market_slippage,
            reduce_only=True,
            dry_run=self.settings.dry_run,
        )
        self.notifier.send_follower_result(
            action=PositionAction.REDUCE,
            coin=leader_pos.coin,
            direction=("LONG" if follower_pos.size > 0 else "SHORT"),
            margin_mode=leader_pos.margin_mode,
            leverage=leader_pos.leverage,
            principal_usd=self.settings.fixed_margin_usd,
            executed_notional_usd=reduce_notional,
            dry_run=self.settings.dry_run,
        )

    def _copy_close(self, coin: str) -> None:
        follower_positions = self.follower_client.get_positions(self.settings.follower_address)
        follower_pos = follower_positions.get(coin)
        if follower_pos is None:
            return

        self.follower_client.close_position_market(
            coin=coin,
            size=follower_pos.size,
            slippage=self.settings.market_slippage,
            dry_run=self.settings.dry_run,
        )

        pnl = None
        if not self.settings.dry_run:
            pnl = self.follower_client.estimate_recent_closed_pnl(self.settings.follower_address)

        self.notifier.send_follower_result(
            action=PositionAction.CLOSE,
            coin=coin,
            direction=("LONG" if follower_pos.size > 0 else "SHORT"),
            margin_mode=follower_pos.margin_mode,
            leverage=follower_pos.leverage,
            principal_usd=self.settings.fixed_margin_usd,
            executed_notional_usd=abs(follower_pos.notional_usd),
            pnl_usd=pnl,
            dry_run=self.settings.dry_run,
        )

    def _check_exposure_limits(self, coin: str, add_principal: float) -> bool:
        follower_positions = self.follower_client.get_positions(self.settings.follower_address)

        open_coins = len(follower_positions)
        is_new_coin = coin not in follower_positions
        if is_new_coin and open_coins >= self.settings.max_open_coins:
            self.notifier.send_text(
                f"open_coins={open_coins}, max_open_coins={self.settings.max_open_coins}, coin={coin}",
                title="风控拦截",
            )
            return False

        total_principal = self._sum_principal(follower_positions)
        if total_principal + add_principal > self.settings.max_total_principal_usd:
            self.notifier.send_text(
                (
                    f"current_principal={total_principal:.4f}U + add={add_principal:.4f}U "
                    f"> max={self.settings.max_total_principal_usd:.4f}U"
                ),
                title="风控拦截",
            )
            return False

        return True

    @staticmethod
    def _sum_principal(positions: Dict[str, PositionSnapshot]) -> float:
        total = 0.0
        for pos in positions.values():
            total += pos.principal_usd
        return total
