import logging
import threading
import time

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
    notifier.send_text(
        engine.build_runtime_status_text(),
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
                notifier.send_text(engine.build_runtime_status_text(), title="系统心跳")
            except Exception as exc:
                logging.exception("发送心跳失败: %s", exc)

    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()

    ws_monitor.start_forever(on_leader_event)


if __name__ == "__main__":
    main()
