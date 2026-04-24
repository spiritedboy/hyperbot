from datetime import datetime
import logging
import time
from typing import Optional

import requests

from hyperbot.models import PositionAction, PositionSnapshot


class FeishuNotifier:
    def __init__(self, webhook: str, timeout: float = 5.0):
        self._webhook = webhook
        self._timeout = timeout

    def send_text(self, text: str, title: Optional[str] = None) -> None:
        full_text = text if not title else f"[{title}]\n{text}"
        payload = {
            "msg_type": "text",
            "content": {
                "text": full_text,
            },
        }
        last_error: Optional[Exception] = None
        for idx in range(3):
            try:
                resp = requests.post(self._webhook, json=payload, timeout=self._timeout)
                resp.raise_for_status()
                return
            except Exception as exc:
                last_error = exc
                logging.warning("飞书推送失败，第 %s 次重试: %s", idx + 1, exc)
                time.sleep(0.4)

        if last_error is not None:
            raise last_error

    def send_leader_signal(self, action: PositionAction, snapshot: PositionSnapshot) -> None:
        ratio_pct = snapshot.principal_ratio * 100
        text = (
            f"[Leader信号] {action.value.upper()}\n"
            f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"币种: {snapshot.coin}\n"
            f"方向: {snapshot.direction}\n"
            f"开仓类型: {snapshot.margin_mode}\n"
            f"杠杆: {snapshot.leverage:.2f}x\n"
            f"开单本金: {snapshot.principal_usd:.4f} U\n"
            f"本金占总余额: {ratio_pct:.2f}%\n"
            f"仓位面额: {abs(snapshot.notional_usd):.4f} U"
        )
        self.send_text(text)

    def send_follower_result(
        self,
        action: PositionAction,
        coin: str,
        direction: str,
        margin_mode: str,
        leverage: float,
        principal_usd: float,
        executed_notional_usd: float,
        pnl_usd: Optional[float] = None,
        dry_run: bool = False,
    ) -> None:
        lines = [
            f"[跟单结果] {action.value.upper()}",
            f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"币种: {coin}",
            f"方向: {direction}",
            f"开仓类型: {margin_mode}",
            f"杠杆: {leverage:.2f}x",
            f"跟单本金: {principal_usd:.4f} U",
            f"下单面额: {executed_notional_usd:.4f} U",
            f"模式: {'DRY_RUN' if dry_run else 'LIVE'}",
        ]
        if pnl_usd is not None:
            lines.append(f"平仓盈亏: {pnl_usd:.4f} U")
        self.send_text("\n".join(lines))
