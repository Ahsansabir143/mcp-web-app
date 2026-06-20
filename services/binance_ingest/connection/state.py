from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum


class ConnectionState(str, Enum):
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTING = "disconnecting"
    DISCONNECTED = "disconnected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"
    STOPPED = "stopped"


@dataclass
class ConnectionInfo:
    connection_id: str
    market_type: str   # "spot" | "futures"
    stream_type: str   # "public" | "private"
    state: ConnectionState = ConnectionState.DISCONNECTED
    connected_at: float = 0.0
    last_message_at: float = 0.0
    messages_received: int = 0
    reconnect_count: int = 0
    error: str | None = None

    def mark_connected(self) -> None:
        self.state = ConnectionState.CONNECTED
        self.connected_at = time.monotonic()
        self.error = None

    def mark_message(self) -> None:
        self.last_message_at = time.monotonic()
        self.messages_received += 1

    def mark_reconnecting(self, error: str | None = None) -> None:
        self.state = ConnectionState.RECONNECTING
        self.reconnect_count += 1
        self.error = error

    def mark_stopped(self) -> None:
        self.state = ConnectionState.STOPPED
