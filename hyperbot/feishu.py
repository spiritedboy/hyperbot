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
        self._send_payload(payload)

    def send_heartbeat_with_links(
        self,
        status_text: str,
        leader_address: str,
        follower_address: str,
        title: str = "系统心跳",
    ) -> None:
        leader_url = f"https://hyperbot.network/trader/{leader_address}"
        follower_url = f"https://hyperbot.network/trader/{follower_address}"

        content_lines = []
        for line in status_text.split("\n"):
            if line.startswith("监控地址: "):
                content_lines.append(
                    [
                        {"tag": "text", "text": "监控地址: "},
                        {"tag": "a", "text": leader_address, "href": leader_url},
                    ]
                )
                continue

            if line.startswith("跟单地址: "):
                content_lines.append(
                    [
                        {"tag": "text", "text": "跟单地址: "},
                        {"tag": "a", "text": follower_address, "href": follower_url},
                    ]
                )
                continue

            content_lines.append([{"tag": "text", "text": line}])

        payload = {
            "msg_type": "post",
            "content": {
                "post": {
                    "zh_cn": {
                        "title": title,
                        "content": content_lines,
                    }
                }
            },
        }
        self._send_payload(payload)

    def _send_payload(self, payload: dict) -> None:
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
        action_map = {
            PositionAction.OPEN: "开仓",
            PositionAction.ADD: "加仓",
            PositionAction.REDUCE: "减仓",
            PositionAction.CLOSE: "清仓",
            PositionAction.FLIP: "反手",
        }
        direction = "做多" if snapshot.direction == "LONG" else "做空"
        margin_mode = "全仓" if str(snapshot.margin_mode).lower() == "cross" else "逐仓"
        pnl_text = "未知" if snapshot.unrealized_pnl_usd is None else f"{snapshot.unrealized_pnl_usd:.4f} U"
        liq_text = "未知" if snapshot.liquidation_price is None else f"{snapshot.liquidation_price:.6f}"
        lines = [
            f"[监控信号] {action_map.get(action, action.value)}",
            f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"币种/方向: {snapshot.coin} / {direction}",
            f"仓位模式/杠杆: {margin_mode} / {snapshot.leverage:.2f}x",
            f"开单本金/仓位面额: {snapshot.principal_usd:.4f} U / {abs(snapshot.notional_usd):.4f} U",
            f"本金占比/当前盈亏: {ratio_pct:.2f}% / {pnl_text}",
            f"爆仓价: {liq_text}",
        ]
        text = "\n".join(lines)
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
        action_map = {
            PositionAction.OPEN: "开仓",
            PositionAction.ADD: "加仓",
            PositionAction.REDUCE: "减仓",
            PositionAction.CLOSE: "清仓",
            PositionAction.FLIP: "反手",
        }
        direction_text = "做多" if direction.upper() == "LONG" else "做空"
        margin_mode_text = "全仓" if str(margin_mode).lower() == "cross" else "逐仓"
        lines = [
            f"[跟单结果] {action_map.get(action, action.value)}",
            f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"币种/方向: {coin} / {direction_text}",
            f"仓位模式/杠杆: {margin_mode_text} / {leverage:.2f}x",
            f"跟单本金/下单面额: {principal_usd:.4f} U / {executed_notional_usd:.4f} U",
            f"模式: {'DRY_RUN' if dry_run else 'LIVE'}",
        ]
        if pnl_usd is not None:
            lines.append(f"平仓盈亏: {pnl_usd:.4f} U")
        self.send_text("\n".join(lines))
