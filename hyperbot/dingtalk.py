"""
钉钉机器人通知模块。

消息类型策略：
  - Markdown：交易信号、跟单结果、风控拦截、系统错误（支持标题/加粗/分隔线/引用块/链接）
  - ActionCard（整体跳转）：启动通知、心跳状态（含 "查看 Leader" 按钮）
"""

import logging
import time
from datetime import datetime
from typing import Optional

import requests

from hyperbot.models import PositionAction, PositionSnapshot

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_ACTION_META: dict[PositionAction, tuple[str, str]] = {
    PositionAction.OPEN:   ("📈", "开仓"),
    PositionAction.ADD:    ("➕", "加仓"),
    PositionAction.REDUCE: ("➖", "减仓"),
    PositionAction.CLOSE:  ("❌", "清仓"),
    PositionAction.FLIP:   ("🔄", "反手"),
}

_HYPERLIQUID_TRADER_URL = "https://app.hyperliquid.xyz/trade"


def _addr_url(address: str) -> str:
    """生成 Hyperliquid 持仓页链接（按地址过滤）"""
    return f"https://app.hyperliquid.xyz/portfolio?address={address}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class DingTalkNotifier:
    """钉钉自定义机器人通知器，与 FeishuNotifier 接口保持一致。"""

    def __init__(self, webhook: str, timeout: float = 5.0) -> None:
        self._webhook = webhook
        self._timeout = timeout

    # ── 公开接口 ─────────────────────────────────────────────────────

    def send_text(self, text: str, title: Optional[str] = None) -> None:
        """发送纯文本通知（Markdown 格式，用于错误 / 风控等简短消息）。"""
        header = f"#### ⚠️ {title}\n\n" if title else ""
        lines = [f"> {line}" if line.strip() else "" for line in text.split("\n")]
        md = f"{header}{'  \n'.join(lines)}"
        self._send_markdown(title or "系统通知", md)

    def send_heartbeat_with_links(
        self,
        status_text: str,
        leader_address: str,
        follower_address: str,
        title: str = "系统心跳",
    ) -> None:
        """以 ActionCard 发送启动/心跳状态，包含可点击的查看按钮。"""
        leader_url = _addr_url(leader_address)
        follower_url = _addr_url(follower_address)

        md_body = self._status_to_markdown(status_text, leader_url, follower_url)
        emoji = "🚀" if "启动" in title else "💓"

        self._send_action_card(
            title=f"{emoji} {title}",
            text=md_body,
            btn_title="查看 Leader 仓位",
            btn_url=leader_url,
        )

    def send_leader_signal(
        self, action: PositionAction, snapshot: PositionSnapshot
    ) -> None:
        """以 Markdown 发送监控到的 Leader 交易信号。"""
        emoji, action_cn = _ACTION_META.get(action, ("📊", action.value))
        direction = "🟢 做多" if str(snapshot.direction).upper() == "LONG" else "🔴 做空"
        margin_mode = "全仓" if str(snapshot.margin_mode).lower() in ("cross", "全仓") else "逐仓"
        pnl_text = (
            "—"
            if snapshot.unrealized_pnl_usd is None
            else f"{snapshot.unrealized_pnl_usd:+.4f} U"
        )
        liq_text = (
            "—"
            if snapshot.liquidation_price is None
            else f"{snapshot.liquidation_price:.4f}"
        )
        entry_text = (
            "—"
            if snapshot.entry_price is None
            else f"{snapshot.entry_price:.4f}"
        )
        ratio_pct = snapshot.principal_ratio * 100
        now = datetime.now().strftime("%H:%M:%S")

        md = (
            f"## {emoji} 监控信号 · {action_cn}\n\n"
            f"---\n\n"
            f"**{snapshot.coin}** &nbsp; {direction} &nbsp; `{margin_mode} {snapshot.leverage:.0f}x` &nbsp; _{now}_\n\n"
            f"> 本金 **{snapshot.principal_usd:.2f} U** &nbsp;｜&nbsp; 面额 {abs(snapshot.notional_usd):.2f} U  \n"
            f"> 本金占比 {ratio_pct:.2f}% &nbsp;｜&nbsp; 浮盈 **{pnl_text}**  \n"
            f"> 开仓价 {entry_text} &nbsp;｜&nbsp; 爆仓价 {liq_text}"
        )
        self._send_markdown(f"监控信号 · {snapshot.coin} {action_cn}", md)

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
        entry_price: Optional[float] = None,
        close_price: Optional[float] = None,
        dry_run: bool = False,
    ) -> None:
        """以 Markdown 发送跟单执行结果。"""
        emoji, action_cn = _ACTION_META.get(action, ("✅", action.value))
        dir_text = "🟢 做多" if direction.upper() == "LONG" else "🔴 做空"
        mode_text = "全仓" if str(margin_mode).lower() in ("cross", "全仓") else "逐仓"
        run_badge = "🧪 **DRY RUN**" if dry_run else "🔴 **LIVE**"
        now = datetime.now().strftime("%H:%M:%S")

        pnl_line = ""
        if pnl_usd is not None:
            pnl_sign = "+" if pnl_usd >= 0 else ""
            pnl_line = f"> 平仓盈亏 **{pnl_sign}{pnl_usd:.4f} U**  \n"

        price_parts = []
        if entry_price is not None:
            price_parts.append(f"开仓价 **{entry_price:.4f}**")
        if close_price is not None:
            price_parts.append(f"平仓价 **{close_price:.4f}**")
        price_line = (f"> {'　｜　'.join(price_parts)}  \n") if price_parts else ""

        md = (
            f"## ✅ 跟单结果 · {action_cn}\n\n"
            f"---\n\n"
            f"**{coin}** &nbsp; {dir_text} &nbsp; `{mode_text} {leverage:.0f}x` &nbsp; {run_badge} &nbsp; _{now}_\n\n"
            f"> 本金 **{principal_usd:.2f} U** &nbsp;｜&nbsp; 面额 {executed_notional_usd:.2f} U  \n"
            f"{price_line}"
            f"{pnl_line}"
        )
        self._send_markdown(f"跟单结果 · {coin} {action_cn}", md)

    # ── 内部发送 ─────────────────────────────────────────────────────

    def _send_markdown(self, title: str, text: str) -> None:
        self._send_payload(
            {"msgtype": "markdown", "markdown": {"title": title, "text": text}}
        )

    def _send_action_card(
        self, title: str, text: str, btn_title: str, btn_url: str
    ) -> None:
        self._send_payload(
            {
                "msgtype": "actionCard",
                "actionCard": {
                    "title": title,
                    "text": text,
                    "singleTitle": btn_title,
                    "singleURL": btn_url,
                    "btnOrientation": "0",
                },
            }
        )

    def _send_payload(self, payload: dict) -> bool:
        """发送 payload，失败最多重试 3 次，最终失败仅记录日志（非致命）。"""
        last_err: Optional[Exception] = None
        for attempt in range(3):
            try:
                resp = requests.post(
                    self._webhook, json=payload, timeout=self._timeout
                )
                resp.raise_for_status()
                body = resp.json()
                if body.get("errcode", 0) != 0:
                    raise RuntimeError(f"钉钉返回错误: {body}")
                return True
            except Exception as exc:
                last_err = exc
                if attempt < 2:
                    logging.warning("钉钉推送失败，第 %d 次重试: %s", attempt + 1, exc)
                    time.sleep(0.4)
        logging.error("钉钉推送最终失败，已放弃本条消息: %s", last_err)
        return False

    # ── 状态文本 → Markdown 转换 ─────────────────────────────────────

    @staticmethod
    def _status_to_markdown(
        status_text: str, leader_url: str, follower_url: str
    ) -> str:
        """将 copier.py 生成的纯文本状态转换为钉钉 Markdown。"""
        lines = status_text.split("\n")
        out: list[str] = []

        for line in lines:
            stripped = line.rstrip()

            # ── 地址标题行 ──────────────────────────────────────────
            if stripped.startswith("监控地址: "):
                addr = stripped[len("监控地址: "):]
                out.append(f"### 🔍 监控地址\n\n[{addr[:6]}...{addr[-4:]}]({leader_url})\n")
                continue

            if stripped.startswith("跟单地址: "):
                addr = stripped[len("跟单地址: "):]
                out.append(f"\n---\n\n### 📋 跟单地址\n\n[{addr[:6]}...{addr[-4:]}]({follower_url})\n")
                continue

            # ── 仓位明细标题 ─────────────────────────────────────────
            if "仓位明细:" in stripped:
                out.append("\n**仓位明细**\n")
                continue

            # ── 仓位条目（"- COIN 方向 | ..."）──────────────────────
            if stripped.startswith("- ") and "|" in stripped:
                out.append(DingTalkNotifier._format_position_line(stripped))
                continue

            # ── 模式标识行（"模式: DRY_RUN / LIVE"）────────────────
            if stripped.startswith("模式: "):
                mode = stripped[len("模式: "):]
                badge = "🧪 DRY RUN" if "DRY" in mode.upper() else "🔴 LIVE"
                out.append(f"\n> {badge}\n")
                continue

            # ── 普通键值行 ───────────────────────────────────────────
            if ": " in stripped:
                key, _, val = stripped.partition(": ")
                for prefix in ("监控地址", "跟单地址"):
                    key = key.replace(prefix, "")
                # 清理括号注释
                key = key.replace("(统一账户)", "").replace("(估算)", "").strip()
                if key:
                    out.append(f"> **{key}** {val}  ")
                continue

            # ── 空行 ─────────────────────────────────────────────────
            if not stripped:
                out.append("")
                continue

            out.append(stripped)

        return "\n".join(out)

    @staticmethod
    def _format_position_line(line: str) -> str:
        """将 '- BTC 做多 | 仓位模式=全仓 | 杠杆=20.00x | ...' 格式化为 Markdown 行。"""
        content = line[2:].strip()
        parts = [p.strip() for p in content.split("|")]

        tokens = parts[0].split()
        coin = tokens[0] if tokens else "?"
        dir_cn = tokens[1] if len(tokens) > 1 else ""
        dir_icon = "🟢" if dir_cn == "做多" else "🔴"

        kv: dict[str, str] = {}
        for part in parts[1:]:
            if "=" in part:
                k, _, v = part.partition("=")
                kv[k.strip()] = v.strip()

        mode = kv.get("仓位模式", "")
        lev = kv.get("杠杆", "").rstrip("x")
        notional = kv.get("仓位面额", kv.get("面额", ""))
        entry = kv.get("开仓价", "")
        pnl = kv.get("当前盈亏", kv.get("浮动盈亏", ""))
        liq = kv.get("爆仓价", "")

        parts_out = [f"**{coin}** {dir_icon} {dir_cn}"]
        if mode or lev:
            parts_out.append(f"`{mode} {lev}x`")
        if notional:
            parts_out.append(f"面额 {notional}")
        if entry:
            parts_out.append(f"开仓 {entry}")
        if pnl:
            parts_out.append(f"盈亏 {pnl}")
        if liq:
            parts_out.append(f"爆仓 {liq}")

        return "- " + "　".join(parts_out)
