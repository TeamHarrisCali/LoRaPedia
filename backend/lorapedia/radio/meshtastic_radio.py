"""Real radio: the official `meshtastic` library over serial, TCP or BLE.

The library is synchronous and delivers packets on its own thread through pypubsub, so
this adapter bridges both directions: receive callbacks hop onto the asyncio loop, and
blocking calls (connect, send, close) run in worker threads.
"""

import asyncio
import glob
import logging
import os
import re
import stat
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
MAX_PAYLOAD = 233  # Meshtastic's hard text limit; the configured limit is always <= this
CONNECT_TIMEOUT_S = 20  # the library's default is 300s, far too long for a UI "Retry"
TRACK_KEEP_S = 600      # a sent message is followed for this long for its acknowledgement
TRACK_MAX = 512
# What the firmware's negative acknowledgements mean, in words for the person running the node.
ROUTING_ERRORS = {
    "MAX_RETRANSMIT": "no acknowledgement after the retries", "NO_ROUTE": "no route to the node", "TIMEOUT": "timed out",
    "NO_CHANNEL": "the node does not share the channel", "PKI_FAILED": "encryption key problem", "PKI_UNKNOWN_PUBKEY": "its public key is unknown",
    "NO_RESPONSE": "no response", "TOO_LARGE": "message too large", "DUTY_CYCLE_LIMIT": "radio duty cycle limit reached",
    "RATE_LIMIT_EXCEEDED": "rate limit reached", "NOT_AUTHORIZED": "not authorized",
}


class RadioError(RuntimeError):
    """Raised with a user-actionable message (shown as a toast in the UI)."""


@dataclass(frozen=True)
class ConnectionSpec:
    method: Literal["serial", "tcp", "ble"]
    address: str = ""  # serial port path, TCP host, or BLE address/name; "" = auto-detect where possible


DEVICE_DIR_ENV = "LORAPEDIA_DEVICE_DIR"
SYSFS_TTY = "/sys/class/tty"


def _usb_vendor(name: str, sysfs: str = SYSFS_TTY) -> int | None:
    """The USB vendor id behind a serial device (ttyACM0 -> 0x239a), read from sysfs; None when it cannot be told."""
    try:
        here = os.path.realpath(os.path.join(sysfs, name, "device"))
        for _ in range(5):  # the vendor id sits a few levels above the interface the tty hangs off
            vid = os.path.join(here, "idVendor")
            if os.path.isfile(vid):
                with open(vid, encoding="ascii") as f:
                    return int(f.read().strip(), 16)
            here = os.path.dirname(here)
    except (OSError, ValueError):
        pass
    return None


def scan_device_dir(root: str, sysfs: str = SYSFS_TTY) -> list[str]:
    """Serial ports among the device files in `root` that look like Meshtastic radios, the way meshtastic's own finder judges them:
    those from a vendor Meshtastic lists first, otherwise anything not known to be something else.

    Used when LORAPEDIA_DEVICE_DIR points somewhere other than /dev: in Docker the host's /dev is mounted beside the container's own,
    so a radio plugged in at any time appears there (and again after it resets) without the container being reconfigured."""
    try:
        from meshtastic.util import blacklistVids, whitelistVids
    except Exception:  # an old library without the lists: judge nothing, offer everything
        whitelistVids, blacklistVids = {}, {}
    found = []
    for pattern in ("ttyACM*", "ttyUSB*"):
        for path in glob.glob(os.path.join(root, pattern)):
            try:
                if stat.S_ISCHR(os.stat(path).st_mode):
                    found.append((path, _usb_vendor(os.path.basename(path), sysfs)))
            except OSError:
                pass
    found.sort()
    likely = [p for p, vid in found if vid is not None and vid in whitelistVids]
    return likely or [p for p, vid in found if vid is None or vid not in blacklistVids]


def list_serial_ports() -> list[str]:
    """Serial ports that look like Meshtastic devices."""
    root = os.environ.get(DEVICE_DIR_ENV, "")
    if root and os.path.realpath(root) != "/dev":
        return scan_device_dir(root)
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
    if ("ermission" in text or "Errno 13" in text) and os.environ.get(DEVICE_DIR_ENV):
        return RadioError(f"Permission denied opening {spec.address or 'the serial port'}. LoRaPedia learns the device's group when its "
                          "container starts, so if the radio was plugged in afterwards, run `docker compose restart lorapedia`.")
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
        on_delivery: Callable[[int, str, str | None], None] | None = None,
        factory: Callable[[ConnectionSpec], Any] = _default_factory,
    ) -> None:
        self.spec = spec
        self._on_delivery = on_delivery  # (packet id, "delivered" | "relayed" | "failed", why), on the event loop
        self._tracked: dict[int, tuple[int | None, float]] = {}  # id of a message we sent -> (its addressee's number, when)
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
        if interface is not self._iface or self._loop is None:
            return
        self._track_ack(packet)
        if self._on_mesh is None:
            return
        try:
            self._loop.call_soon_threadsafe(self._on_mesh, packet, self._my_num())
        except RuntimeError:  # the loop has closed (shutting down)
            pass

    def _track(self, packet_id: int, to: str) -> None:
        """Remember a message we sent that asked for an acknowledgement, so the answer can be matched to it."""
        now = time.time()
        for old in [i for i, (_, at) in self._tracked.items() if now - at > TRACK_KEEP_S][:64]:
            self._tracked.pop(old, None)
        while len(self._tracked) >= TRACK_MAX:
            self._tracked.pop(next(iter(self._tracked)), None)
        try:
            dest = int(to[1:], 16)
        except ValueError:
            dest = None
        self._tracked[packet_id] = (dest, now)

    def _track_ack(self, packet: dict) -> None:
        """Match a routing packet to a message we sent (meshtastic thread). An acknowledgement from the addressee itself means it
        arrived; one from anyone else (a neighbour that heard it being passed on) means only that it left. A NAK means it failed."""
        decoded = packet.get("decoded") or {}
        rid = decoded.get("requestId")
        if rid is None or decoded.get("portnum") != "ROUTING_APP" or rid not in self._tracked:
            return
        dest = self._tracked[rid][0]
        reason = (decoded.get("routing") or {}).get("errorReason") or "NONE"
        if reason == "NONE":
            state, why = ("delivered" if dest is not None and packet.get("from") == dest else "relayed"), None
        else:
            state, why = "failed", ROUTING_ERRORS.get(reason, reason.lower().replace("_", " "))
        if state != "relayed":
            self._tracked.pop(rid, None)  # settled; a relayed one may still be followed by the real acknowledgement
        if self._on_delivery is not None and self._loop is not None:
            try:
                self._loop.call_soon_threadsafe(self._on_delivery, rid, state, why)
            except RuntimeError:
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

    async def send_text(self, text: str, to: str | None, channel: int, hop_limit: int) -> int | None:
        """Send a direct message and return its packet id (what its acknowledgement will refer to), or None if the library gave none."""
        if self._iface is None:
            raise RadioError("Radio is not connected.")
        if to is None:  # the lowest layer of the same rule: this adapter has no way to broadcast
            raise RadioError("LoRaPedia never posts to a public channel: a message needs an addressee.")
        if byte_len(text) > MAX_PAYLOAD:  # last line of defence; the fitter keeps us well below this
            raise RadioError(f"Refusing to send {byte_len(text)} bytes (limit {MAX_PAYLOAD}).")
        iface = self._iface

        def send() -> int | None:
            sent = iface.sendText(text, destinationId=to, wantAck=True, channelIndex=channel, hopLimit=hop_limit)
            packet_id = getattr(sent, "id", None)
            if isinstance(packet_id, int) and packet_id:
                self._track(packet_id, to)  # before the thread returns: an acknowledgement can only come after the radio has sent it
                return packet_id
            return None

        return await asyncio.to_thread(send)

    def local_node(self):
        """The library's object for this radio's own settings. Only the Radio settings page (radio/config.py) asks for it."""
        if self._iface is None:
            raise RadioError("The radio isn't connected.")
        return self._iface.localNode

    async def post_public(self, text: str, channel: int, hop_limit: int) -> None:
        """Post to a public channel. ONLY the operator's Messages page calls this, when a person pressed the button (see messaging.py):
        nothing the engine does can reach it. It is deliberately a different method from `send_text`, which can never broadcast."""
        if self._iface is None:
            raise RadioError("Radio is not connected.")
        if byte_len(text) > MAX_PAYLOAD:
            raise RadioError(f"Refusing to send {byte_len(text)} bytes (limit {MAX_PAYLOAD}).")
        if not isinstance(channel, int) or isinstance(channel, bool) or not 0 <= channel <= 7:
            raise RadioError("That is not a channel.")
        await asyncio.to_thread(self._iface.sendText, text, destinationId="^all", wantAck=False, channelIndex=channel, hopLimit=hop_limit)

    async def traceroute(self, node_id: str, hop_limit: int, timeout: float) -> dict:
        """Send one traceroute request to ONE node and return the reply packet. Raises RadioError, or TimeoutError when nobody answers.

        Addressed to a single node and never to everyone; the caller (mesh/traceroute.py) decides whether one may be sent now."""
        if self._iface is None:
            raise RadioError("Radio is not connected.")
        if not isinstance(node_id, str) or not re.fullmatch(r"![0-9a-f]{8}", node_id) or node_id == "!ffffffff":
            raise RadioError("A traceroute goes to one node, never to everyone.")
        from meshtastic.protobuf import mesh_pb2, portnums_pb2
        loop, iface = asyncio.get_running_loop(), self._iface
        fut = loop.create_future()

        def on_response(packet: dict) -> None:  # runs on the meshtastic thread
            decoded = packet.get("decoded") or {}
            if decoded.get("portnum") == "ROUTING_APP" and (decoded.get("routing") or {}).get("errorReason", "NONE") == "NONE":
                return  # a plain acknowledgement, not the answer
            loop.call_soon_threadsafe(lambda: fut.done() or fut.set_result(packet))

        def send() -> None:
            iface.sendData(mesh_pb2.RouteDiscovery(), destinationId=node_id, portNum=portnums_pb2.PortNum.TRACEROUTE_APP,
                           wantResponse=True, onResponse=on_response, channelIndex=0, hopLimit=hop_limit)

        try:
            await asyncio.to_thread(send)
        except Exception as exc:
            raise RadioError(f"Could not send the traceroute ({exc.__class__.__name__}).") from exc
        return await asyncio.wait_for(fut, timeout)

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
