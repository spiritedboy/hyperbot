import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    api_url: str
    leader_address: str
    follower_private_key: str
    follower_address: str
    feishu_webhook: str
    fixed_margin_usd: float
    ws_reconnect_seconds: float
    market_slippage: float
    event_debounce_seconds: float
    duplicate_ttl_seconds: float
    max_open_coins: int
    max_total_principal_usd: float
    min_notional_usd: float
    allow_short: bool
    heartbeat_seconds: float
    dry_run: bool


def _parse_bool(raw: str, default: bool) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_settings() -> Settings:
    load_dotenv()

    api_url = os.getenv("HL_API_URL", "https://api.hyperliquid.xyz").strip()
    leader_address = os.getenv("LEADER_ADDRESS", "").strip()
    follower_private_key = os.getenv("FOLLOWER_PRIVATE_KEY", "").strip()
    follower_address = os.getenv("FOLLOWER_ADDRESS", "").strip()
    feishu_webhook = os.getenv("FEISHU_WEBHOOK", "").strip()

    if not leader_address:
        raise ValueError("LEADER_ADDRESS 未配置")
    if not follower_private_key:
        raise ValueError("FOLLOWER_PRIVATE_KEY 未配置")
    if not follower_address:
        raise ValueError("FOLLOWER_ADDRESS 未配置")
    if not feishu_webhook:
        raise ValueError("FEISHU_WEBHOOK 未配置")

    fixed_margin_usd = float(os.getenv("FIXED_MARGIN_USD", "20"))
    ws_reconnect_seconds = float(os.getenv("WS_RECONNECT_SECONDS", "3"))
    market_slippage = float(os.getenv("MARKET_SLIPPAGE", "0.03"))
    event_debounce_seconds = float(os.getenv("EVENT_DEBOUNCE_SECONDS", "0.5"))
    duplicate_ttl_seconds = float(os.getenv("DUPLICATE_TTL_SECONDS", "2"))
    max_open_coins = int(os.getenv("MAX_OPEN_COINS", "6"))
    max_total_principal_usd = float(os.getenv("MAX_TOTAL_PRINCIPAL_USD", "200"))
    min_notional_usd = float(os.getenv("MIN_NOTIONAL_USD", "5"))
    allow_short = _parse_bool(os.getenv("ALLOW_SHORT"), default=True)
    heartbeat_seconds = float(os.getenv("HEARTBEAT_SECONDS", "300"))
    dry_run = _parse_bool(os.getenv("DRY_RUN"), default=False)

    return Settings(
        api_url=api_url,
        leader_address=leader_address,
        follower_private_key=follower_private_key,
        follower_address=follower_address,
        feishu_webhook=feishu_webhook,
        fixed_margin_usd=fixed_margin_usd,
        ws_reconnect_seconds=ws_reconnect_seconds,
        market_slippage=market_slippage,
        event_debounce_seconds=event_debounce_seconds,
        duplicate_ttl_seconds=duplicate_ttl_seconds,
        max_open_coins=max_open_coins,
        max_total_principal_usd=max_total_principal_usd,
        min_notional_usd=min_notional_usd,
        allow_short=allow_short,
        heartbeat_seconds=heartbeat_seconds,
        dry_run=dry_run,
    )
