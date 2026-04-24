import logging
import threading
import time

from eth_account import Account

from hyperbot.config import load_settings
from hyperbot.copier import CopyTradingEngine
from hyperbot.feishu import FeishuNotifier
from hyperbot.hyperliquid_client import HyperliquidClient
from hyperbot.ws_monitor import LeaderWsMonitor


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)


def main() -> None:
    settings = load_settings()

    api_wallet_address = Account.from_key(settings.follower_private_key).address.lower()
    follower_address = settings.follower_address.lower()

    leader_client = HyperliquidClient(api_url=settings.api_url)
    follower_client = HyperliquidClient(
        api_url=settings.api_url,
        private_key=settings.follower_private_key,
    )
    notifier = FeishuNotifier(settings.feishu_webhook)

    engine = CopyTradingEngine(settings, leader_client, follower_client, notifier)
    engine.bootstrap()
    ws_monitor = LeaderWsMonitor(
        api_url=settings.api_url,
        leader_address=settings.leader_address,
        reconnect_seconds=settings.ws_reconnect_seconds,
    )

    logging.info("Hyperliquid WS 跟单系统已启动, dry_run=%s", settings.dry_run)
    if follower_address == api_wallet_address:
        warn_text = (
            "检测到 FOLLOWER_ADDRESS 与 API 钱包地址相同。\n"
            "这通常会导致余额显示为 0，且容易引发账户识别错误。\n"
            "请将 FOLLOWER_ADDRESS 改为 Hyperliquid 主账户地址，"
            "FOLLOWER_PRIVATE_KEY 保持为 API 钱包私钥。"
        )
        logging.warning(warn_text)
        notifier.send_text(warn_text, title="配置警告")

    notifier.send_heartbeat_with_links(
        status_text=engine.build_runtime_status_text(),
        leader_address=settings.leader_address,
        follower_address=settings.follower_address,
        title="系统启动",
    )

    tick_lock = threading.Lock()
    pending_lock = threading.Lock()
    pending_tick = {"value": False}

    def on_leader_event() -> None:
        if not tick_lock.acquire(blocking=False):
            with pending_lock:
                pending_tick["value"] = True
            return

        try:
            while True:
                with pending_lock:
                    pending_tick["value"] = False
                engine.tick()
                with pending_lock:
                    if not pending_tick["value"]:
                        break
        except Exception as exc:
            logging.exception("WS 事件处理失败: %s", exc)
            try:
                notifier.send_text(f"WS 事件处理失败: {exc}", title="系统异常")
            except Exception:
                logging.exception("飞书告警发送失败")
        finally:
            tick_lock.release()

    def heartbeat_loop() -> None:
        while True:
            if settings.heartbeat_seconds <= 0:
                return
            time.sleep(settings.heartbeat_seconds)
            try:
                notifier.send_heartbeat_with_links(
                    status_text=engine.build_runtime_status_text(),
                    leader_address=settings.leader_address,
                    follower_address=settings.follower_address,
                    title="系统心跳",
                )
            except Exception as exc:
                logging.exception("发送心跳失败: %s", exc)

    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()

    ws_monitor.start_forever(on_leader_event)


if __name__ == "__main__":
    main()
