"""Real radio: the official `meshtastic` library over serial, TCP or BLE.

The library is synchronous and delivers packets on its own thread through pypubsub, so
this adapter bridges both directions: receive callbacks hop onto the asyncio loop, and
blocking calls (connect, send, close) run in worker threads.
"""

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal

from pubsub import pub

from ..fitter import byte_len
from ..mesh.observe import node_row
from .base import Packet

log = logging.getLogger(__name__)

BROADCAST_NUM = 0xFFFFFFFF  # meshtastic.BROADCAST_NUM
BROADCAST_ADDR = "^all"  # meshtastic.BROADCAST_ADDR
MAX_PAYLOAD = 233  # Meshtastic's hard text limit; the configured limit is always <= this
CONNECT_TIMEOUT_S = 20  # the library's default is 300s, far too long for a UI "Retry"


class RadioError(RuntimeError):
    """Raised with a user-actionable message (shown as a toast in the UI)."""


@dataclass(frozen=True)
class ConnectionSpec:
    method: Literal["serial", "tcp", "ble"]
    address: str = ""  # serial port path, TCP host, or BLE address/name; "" = auto-detect where possible


def list_serial_ports() -> list[str]:
    """Serial ports that look like Meshtastic devices."""
    try:
        from meshtastic.util import findPorts
        return list(findPorts(True))
    except Exception:
        return []


def _default_factory(spec: ConnectionSpec) -> Any:
    """Open a meshtastic interface. Blocking; run in a thread."""
    if spec.method == "serial":
        from meshtastic.serial_interface import SerialInterface
        port = spec.address
        if not port:
            # Detect ourselves: the library calls sys.exit() on zero or several ports.
            ports = list_serial_ports()
            if not ports:
                raise RadioError("No radio found. Is it plugged in? Check the USB cable (it must carry data).")
            if len(ports) > 1:
                raise RadioError(f"Several radios found ({', '.join(ports)}). Pick one in Radio settings.")
            port = ports[0]
        return SerialInterface(devPath=port, timeout=CONNECT_TIMEOUT_S)
    if spec.method == "tcp":
        from meshtastic.tcp_interface import TCPInterface
        if not spec.address:
            raise RadioError("Enter the radio's IP address or hostname.")
        return TCPInterface(hostname=spec.address, timeout=CONNECT_TIMEOUT_S)
    if spec.method == "ble":
        from meshtastic.ble_interface import BLEInterface
        return BLEInterface(spec.address or None, timeout=CONNECT_TIMEOUT_S)
    raise RadioError(f"Unknown connection method '{spec.method}'.")


def _friendly(exc: BaseException, spec: ConnectionSpec) -> RadioError:
    text = str(exc)
    if isinstance(exc, RadioError):
        return exc
    if "ermission" in text or "Errno 13" in text:
        return RadioError(f"Permission denied opening {spec.address or 'the serial port'}. Add your user to the "
                          "'dialout' group (or 'uucp' on Arch), then log out and back in.")
    if "busy" in text.lower() or "Errno 16" in text:
        return RadioError("The serial port is busy. Close other apps using the radio (Meshtastic app, CLI, "
                          "ModemManager) and retry.")
    if spec.method == "tcp" and isinstance(exc, OSError):  # refused, unreachable, or unresolvable host
        return RadioError(f"Cannot reach {spec.address}. Check the address, that the radio is on the same network, "
                          "and that WiFi/TCP is enabled in its settings.")
    if isinstance(exc, SystemExit) or "Timed out" in text or "timeout" in text.lower():
        return RadioError("The radio did not respond. Check it is powered, in the right mode, and not in use elsewhere.")
    return RadioError(f"Could not connect to the radio: {text or exc.__class__.__name__}")


class MeshtasticRadio:
    kind = "meshtastic"

    def __init__(
        self,
        spec: ConnectionSpec,
        *,
        on_packet: Callable[[Packet, dict], Awaitable[None]],
        on_lost: Callable[[], None] | None = None,
        on_mesh: Callable[[dict, int | None], None] | None = None,
        factory: Callable[[ConnectionSpec], Any] = _default_factory,
    ) -> None:
        self.spec = spec
        self.node_id = ""
        self.last_rx: dict = {}
        self._on_packet, self._on_lost, self._on_mesh, self._factory = on_packet, on_lost, on_mesh, factory
        self._iface: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._seen: deque[tuple] = deque(maxlen=256)  # the mesh occasionally redelivers a packet

    @property
    def connected(self) -> bool:
        return self._iface is not None

    # ---- lifecycle ----------------------------------------------------------------------

    async def connect(self) -> None:
        self._loop = asyncio.get_running_loop()
        try:
            iface = await asyncio.to_thread(self._factory, self.spec)
        except BaseException as exc:  # SystemExit included: the library exits on some failures
            if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)):
                raise
            raise _friendly(exc, self.spec) from exc
        self._iface = iface
        self.node_id = self._own_id(iface)
        pub.subscribe(self._on_text, "meshtastic.receive.text")
        pub.subscribe(self._on_any, "meshtastic.receive")  # every packet: positions, telemetry, node info (for the mesh database)
        pub.subscribe(self._on_connection_lost, "meshtastic.connection.lost")
        log.info("radio connected: %s (%s)", self.node_id, self.spec)

    async def close(self) -> None:
        iface, self._iface = self._iface, None
        for listener, topic in ((self._on_text, "meshtastic.receive.text"), (self._on_any, "meshtastic.receive"),
                                (self._on_connection_lost, "meshtastic.connection.lost")):
            try:
                pub.unsubscribe(listener, topic)
            except Exception:
                pass
        if iface is not None:
            try:
                await asyncio.to_thread(iface.close)
            except Exception:
                log.debug("error closing radio", exc_info=True)

    @staticmethod
    def _own_id(iface: Any) -> str:
        try:
            return iface.getMyNodeInfo()["user"]["id"]
        except Exception:
            return f"!{iface.myInfo.my_node_num:08x}"

    def _my_num(self) -> int | None:
        try:
            return self._iface.myInfo.my_node_num
        except Exception:
            return None

    # ---- receive (runs on the meshtastic publishing thread) ------------------------------

    def _on_text(self, packet: dict, interface: Any) -> None:
        if interface is not self._iface or self._loop is None:
            return
        parsed = self.to_packet(packet)
        if parsed is None:
            return
        meta = {"rssi": packet.get("rxRssi"), "snr": packet.get("rxSnr"), "hops": packet.get("hopStart", 0)
                - packet.get("hopLimit", 0) if "hopStart" in packet else None, "ts": time.time()}
        self.last_rx = meta
        asyncio.run_coroutine_threadsafe(self._on_packet(parsed, meta), self._loop)

    def _on_any(self, packet: dict, interface: Any) -> None:
        """Every received packet, on the meshtastic thread. Only hands it to the event loop: parsing happens there."""
        if self._on_mesh is None or interface is not self._iface or self._loop is None:
            return
        try:
            self._loop.call_soon_threadsafe(self._on_mesh, packet, self._my_num())
        except RuntimeError:  # the loop has closed (shutting down)
            pass

    def _on_connection_lost(self, interface: Any) -> None:
        if interface is self._iface:
            log.warning("radio connection lost")
            self._iface = None
            if self._on_lost:
                self._on_lost()

    def to_packet(self, packet: dict) -> Packet | None:
        """Convert a meshtastic text packet dict; None for anything we should not answer."""
        text = (packet.get("decoded") or {}).get("text")
        if not text:
            return None
        key = (packet.get("from"), packet.get("id"))
        if packet.get("id") is not None:
            if key in self._seen:
                return None
            self._seen.append(key)
        to = packet.get("to", 0)
        my_num = self._my_num()
        if to == BROADCAST_NUM:
            is_dm = False
        elif my_num is not None and to == my_num:
            is_dm = True
        else:
            return None  # addressed to some other node; not ours to answer
        sender = packet.get("fromId") or (f"!{packet['from']:08x}" if "from" in packet else None)
        if not sender:
            return None
        hops = packet["hopStart"] - packet.get("hopLimit", 0) if packet.get("hopStart") else None  # hopStart is 0 on older firmware
        return Packet(sender, self._short_name(sender), text, is_dm, packet.get("channel", 0),
                      snr=packet.get("rxSnr"), rssi=packet.get("rxRssi"), hops=hops if hops is not None and hops >= 0 else None)

    def _short_name(self, node_id: str) -> str:
        try:
            return self._iface.nodes[node_id]["user"]["shortName"]
        except Exception:
            return node_id[-4:]

    # ---- send ---------------------------------------------------------------------------

    async def send_text(self, text: str, to: str | None, channel: int, hop_limit: int) -> None:
        if self._iface is None:
            raise RadioError("Radio is not connected.")
        if byte_len(text) > MAX_PAYLOAD:  # last line of defence; the fitter keeps us well below this
            raise RadioError(f"Refusing to send {byte_len(text)} bytes (limit {MAX_PAYLOAD}).")
        iface = self._iface
        await asyncio.to_thread(
            iface.sendText, text, destinationId=to or BROADCAST_ADDR, wantAck=to is not None,
            channelIndex=channel, hopLimit=hop_limit)

    # ---- info for the UI ----------------------------------------------------------------

    def nodes(self) -> list[dict]:
        """The radio's node list, flattened and checked (see mesh.observe.node_row). The first keys are what the Radio page uses."""
        if self._iface is None:
            return []
        now, my_num, out = time.time(), self._my_num(), []
        for key, entry in list((getattr(self._iface, "nodes", None) or {}).items()):
            r = node_row(entry, my_num, now, key)
            if r is None:
                continue
            out.append({**r, "short": r["short"] or r["id"][-4:], "long": r["name"] or "", "hops_away": r["hops"], "battery": r["battery"],
                        "last_heard": r["last_heard"], "is_self": r["id"] == self.node_id})
        return sorted(out, key=lambda d: -(d["last_heard"] or 0))

    def channels(self) -> list[dict]:
        try:
            chans = self._iface.localNode.channels or []
            return [{"index": c.index, "name": c.settings.name or ("Primary" if c.index == 0 else f"Channel {c.index}")}
                    for c in chans if c.role != 0]  # role 0 = DISABLED
        except Exception:
            return []

    def device_info(self) -> dict:
        try:
            user = self._iface.getMyNodeInfo().get("user", {})
            return {"short_name": user.get("shortName"), "long_name": user.get("longName"), "hw": user.get("hwModel")}
        except Exception:
            return {}
