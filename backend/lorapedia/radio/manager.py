"""Owns the active radio: a real Meshtastic device when one is configured/found, else the simulator.

Acts as the engine's `Radio`, delegating to whichever is active. A supervisor task keeps
the connection alive (reconnect with backoff) and, in "auto" mode, picks up a device that
is plugged in later.
"""

import asyncio
import logging
from typing import Any, Awaitable, Callable

from ..config import Settings
from .base import Packet
from .meshtastic_radio import ConnectionSpec, MeshtasticRadio, RadioError, _default_factory, list_serial_ports
from .simulator import SimulatorRadio

log = logging.getLogger(__name__)

NO_RADIO = "No radio found. Is it plugged in?"


class RadioManager:
    def __init__(
        self,
        *,
        settings: Callable[[], Settings],
        submit: Callable[[Packet], Awaitable[Any]],
        emit: Callable[[dict], Awaitable[None]] | None = None,
        on_mesh: Callable[[dict, int | None], None] | None = None,
        factory: Callable[[ConnectionSpec], Any] = _default_factory,
        port_lister: Callable[[], list[str]] = list_serial_ports,
        poll_s: float = 15.0,
    ) -> None:
        self.sim = SimulatorRadio()
        self._on_mesh = on_mesh
        self._settings, self._submit, self._emit = settings, submit, emit
        self._factory, self._list_ports, self.poll_s = factory, port_lister, poll_s
        self._real: MeshtasticRadio | None = None
        self.error: str | None = None
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._last_status: dict | None = None
        self._failures = 0

    # ---- Radio protocol (what the engine sees) ------------------------------------------

    @property
    def _current(self):
        return self._real if self._real and self._real.connected else self.sim

    @property
    def node_id(self) -> str:
        return self._current.node_id

    async def send_text(self, text: str, to: str | None, channel: int, hop_limit: int) -> None:
        """What the engine sends through. A reply always has an addressee: nothing here can post to a public channel."""
        if to is None:
            raise RadioError("LoRaPedia never posts to a public channel: a reply goes only to the person who asked.")
        await self._current.send_text(text, to, channel, hop_limit)

    # ---- lifecycle ----------------------------------------------------------------------

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.create_task(self._supervise())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self._real:
            await self._real.close()
            self._real = None

    def wake(self) -> None:
        """Re-evaluate now (settings changed, or the user pressed Retry)."""
        self._wake.set()

    async def retry(self) -> dict:
        await self.reconcile()
        return self.status()

    async def _supervise(self) -> None:
        while True:
            try:
                await self.reconcile()
            except Exception:
                log.exception("radio supervisor error")
            delay = self.poll_s if not self._failures else min(5 * 2 ** (self._failures - 1), 60)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    def _desired(self) -> tuple[ConnectionSpec | None, str | None]:
        """(connection to use, or None for the simulator; message explaining why, if any)."""
        s = self._settings()
        if s.radio_method == "simulator":
            return None, None
        if s.radio_method == "auto":
            ports = self._list_ports()
            if not ports:
                return None, NO_RADIO
            if len(ports) > 1:
                return None, f"Several radios found ({', '.join(ports)}). Pick one in Radio settings."
            return ConnectionSpec("serial", ports[0]), None
        return ConnectionSpec(s.radio_method, s.radio_address), None

    async def reconcile(self) -> None:
        spec, why = self._desired()
        real = self._real
        if real and (not real.connected or real.spec != spec):
            await real.close()  # lost, or the user changed the connection
            self._real = real = None
        if spec is None:
            self.error = why
            self._failures = 0
        elif real is None:
            candidate = MeshtasticRadio(spec, on_packet=self._deliver, on_lost=self._lost, on_mesh=self._on_mesh, factory=self._factory)
            try:
                await candidate.connect()
                self._real, self.error, self._failures = candidate, None, 0
            except RadioError as exc:
                self.error, self._failures = str(exc), self._failures + 1
        await self._publish_status()

    def _lost(self) -> None:  # called on the meshtastic thread
        if self._loop:
            self._loop.call_soon_threadsafe(self._wake.set)

    # ---- events -------------------------------------------------------------------------

    async def _deliver(self, packet: Packet, meta: dict) -> None:
        await self._emit_event({"type": "rx", "from": packet.sender_id, "name": packet.sender_name,
                                "dm": packet.is_dm, **meta})
        await self._submit(packet)

    async def _emit_event(self, event: dict) -> None:
        if self._emit:
            await self._emit(event)

    async def _publish_status(self) -> None:
        status = self.status()
        comparable = {k: v for k, v in status.items() if k not in ("last_rx", "rssi", "snr")}
        if comparable != self._last_status:
            self._last_status = comparable
            await self._emit_event({"type": "radio", "status": status})

    # ---- info for the UI ----------------------------------------------------------------

    @property
    def active(self) -> str:
        return "meshtastic" if self._real and self._real.connected else "simulator"

    def status(self) -> dict:
        s = self._settings()
        real = self._real if self._real and self._real.connected else None
        return {
            "active": self.active, "connected": real is not None, "method": s.radio_method,
            "address": real.spec.address if real else s.radio_address, "node_id": self.node_id,
            "error": self.error, "device": real.device_info() if real else {},
            "last_rx": (real.last_rx.get("ts") if real else None),
            "rssi": real.last_rx.get("rssi") if real else None, "snr": real.last_rx.get("snr") if real else None,
        }

    def nodes(self) -> list[dict]:
        return self._real.nodes() if self._real and self._real.connected else []

    def channels(self) -> list[dict]:
        return self._real.channels() if self._real and self._real.connected else []

    def ports(self) -> list[str]:
        return self._list_ports()
