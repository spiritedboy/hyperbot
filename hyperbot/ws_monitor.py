from __future__ import annotations

import json
import logging
import threading
import time
from typing import Callable
from urllib.parse import urlparse

from websocket import WebSocketApp


class LeaderWsMonitor:
    def __init__(self, api_url: str, leader_address: str, reconnect_seconds: float = 3.0):
        self.api_url = api_url
        self.leader_address = leader_address
        self.reconnect_seconds = reconnect_seconds
        self._stop_event = threading.Event()

    def start_forever(self, on_event: Callable[[], None]) -> None:
        ws_url = self._to_ws_url(self.api_url)

        while not self._stop_event.is_set():
            app = WebSocketApp(
                ws_url,
                on_open=lambda ws: self._on_open(ws),
                on_message=lambda ws, msg: self._on_message(msg, on_event),
                on_error=lambda ws, err: self._on_error(err),
                on_close=lambda ws, code, msg: self._on_close(code, msg),
            )

            try:
                # ping 让连接更稳定，断开后自动回到 while 做重连
                app.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as exc:
                logging.exception("WebSocket run_forever 异常: %s", exc)

            if self._stop_event.is_set():
                break

            logging.warning("WebSocket 已断开，%.1f 秒后重连", self.reconnect_seconds)
            time.sleep(self.reconnect_seconds)

    def stop(self) -> None:
        self._stop_event.set()

    def _on_open(self, ws: WebSocketApp) -> None:
        subs = [
            {"type": "userFills", "user": self.leader_address},
            {"type": "clearinghouseState", "user": self.leader_address},
            {"type": "userEvents", "user": self.leader_address},
        ]
        for sub in subs:
            ws.send(json.dumps({"method": "subscribe", "subscription": sub}))
        logging.info("WebSocket 已连接并订阅 leader 地址事件")

    def _on_message(self, raw_msg: str, on_event: Callable[[], None]) -> None:
        try:
            msg = json.loads(raw_msg)
        except json.JSONDecodeError:
            return

        channel = str(msg.get("channel", ""))
        if channel == "subscriptionResponse":
            return

        if channel in {"userFills", "clearinghouseState", "userEvents"}:
            data = msg.get("data", {})
            if isinstance(data, dict) and data.get("isSnapshot") is True:
                return
            on_event()

    @staticmethod
    def _on_error(err: Exception) -> None:
        logging.error("WebSocket 错误: %s", err)

    @staticmethod
    def _on_close(code: int, message: str) -> None:
        logging.warning("WebSocket 连接关闭 code=%s message=%s", code, message)

    @staticmethod
    def _to_ws_url(api_url: str) -> str:
        parsed = urlparse(api_url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError(f"不支持的 API URL: {api_url}")

        scheme = "wss" if parsed.scheme == "https" else "ws"
        host = parsed.netloc
        return f"{scheme}://{host}/ws"
