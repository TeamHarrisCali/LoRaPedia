"""Read, back up and change the settings stored on our own radio (the Radio settings page).

Operator-only: nothing the AI says and nothing received over the air can reach this module; it is called only by the web API, and its
writes are off until you turn them on in Settings. The form is generated from the radio's own protobuf definitions, so every field is
type-checked and range-checked before anything is sent, and a change is all-or-nothing:

  pull     -> read the settings the radio reported when we connected, and keep a copy
  save     -> validate every change, keep a copy of the current settings, then write only the sections that changed
  restore  -> save, using the values of an earlier copy

Sections that hold secrets are never shown, saved or written: Wi-Fi (the password), security (the radio's private key and admin keys) and
MQTT (credentials); nor is the Bluetooth pairing PIN. Channels (their keys) are not handled here.
Ported from the MeshtasticLLM project (same author, MIT).
"""
import math
import struct
import threading
import unicodedata
from typing import Any, Callable

from google.protobuf.descriptor import FieldDescriptor as FD

from ..store import Store

# ---- which sections and fields are allowed ------------------------------------------------------------------------------------
# Only the sections listed here can be viewed or changed; everything else is refused by _message(). This is an allow-list, so a section
# added by a newer firmware stays invisible until someone adds it deliberately.
CONFIG_SECTIONS = {"device": "Device", "position": "Position", "power": "Power", "display": "Display", "lora": "LoRa radio", "bluetooth": "Bluetooth"}
MODULE_SECTIONS = {"telemetry": "Telemetry", "neighbor_info": "Neighbour info", "store_forward": "Store & forward", "range_test": "Range test",
                   "external_notification": "External notification"}
SECTIONS = {**{k: ("config", v) for k, v in CONFIG_SECTIONS.items()}, **{k: ("module", v) for k, v in MODULE_SECTIONS.items()}}
HIDDEN_FIELDS = {("bluetooth", "fixed_pin")}  # the pairing PIN is a credential: never shown, saved or changed here
EXCLUDED = {"network": "holds the Wi-Fi password", "security": "holds the radio's private key", "mqtt": "holds server credentials"}
# Extra bounds where the protobuf type (uint32) allows nonsense.
LIMITS = {("lora", "hop_limit"): (0, 7), ("lora", "tx_power"): (0, 30), ("lora", "channel_num"): (0, 255)}
MAX_STRING = 200
BACKUP_KEEP = 40
LONG_NAME_MAX_BYTES = 39
SHORT_NAME_MAX = 4

# Changes that can cut the radio off from the mesh, break the law, or lock you out: shown in the confirmation.
RISKS = {
    ("lora", "region"): "A wrong region can be illegal where you are and stops the radio talking to your mesh.",
    ("lora", "modem_preset"): "Radios with a different preset cannot hear this one.",
    ("lora", "use_preset"): "Radios with different LoRa settings cannot hear this one.",
    ("lora", "bandwidth"): "Radios with different LoRa settings cannot hear this one.",
    ("lora", "spread_factor"): "Radios with different LoRa settings cannot hear this one.",
    ("lora", "coding_rate"): "Radios with different LoRa settings cannot hear this one.",
    ("lora", "channel_num"): "Radios on a different channel number cannot hear this one.",
    ("lora", "frequency_offset"): "Shifts the frequency; radios will not hear this one.",
    ("lora", "tx_enabled"): "With this off the radio stops transmitting: no replies, no relaying.",
    ("lora", "tx_power"): "Transmit power above the legal limit for your region is illegal.",
    ("lora", "hop_limit"): "More hops means more airtime used by every node on the mesh.",
    ("device", "role"): "The role changes how the radio relays and broadcasts; ROUTER-type roles belong on well-placed nodes only.",
    ("device", "rebroadcast_mode"): "Changes which packets this radio relays.",
    ("bluetooth", "enabled"): "With Bluetooth off the phone app cannot connect (USB and Wi-Fi are not affected).",
    ("position", "gps_mode"): "Changes whether this radio shares a position with the whole mesh.",
    ("position", "fixed_position"): "A fixed position is shared with the whole mesh, and shows where this radio is.",
}
HELP = {
    ("lora", "region"): "Frequency plan for where you are (US, EU_868, ...). Must match your country.",
    ("lora", "hop_limit"): "How many times a packet may be relayed (3 is the default; 7 is the maximum).",
    ("position", "position_broadcast_secs"): "How often the radio shares its position. 0 = the default.",
    ("device", "node_info_broadcast_secs"): "How often the radio announces itself (minimum 3600 s = 1 hour).",
    ("telemetry", "device_update_interval"): "Seconds between battery/airtime broadcasts. 0 = the default (30 min).",
    ("telemetry", "environment_update_interval"): "Seconds between sensor broadcasts. 0 = the default.",
}


class ConfigError(ValueError):
    """A change the code refuses; the message is safe to show. `status` is the HTTP code the API answers with."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def pretty(name: str) -> str:
    return name.replace("_", " ").capitalize()


def _message(node, section: str):
    """The live protobuf message for an allowed section. Refuses any section not in SECTIONS, which keeps the secret-bearing ones unreachable."""
    if section not in SECTIONS:
        raise ConfigError(f"'{str(section)[:40]}' can't be viewed or changed here.")
    if getattr(node, "localConfig", None) is None or getattr(node, "moduleConfig", None) is None:
        raise ConfigError("The radio hasn't sent its settings yet.", 409)
    return getattr(node.localConfig if SECTIONS[section][0] == "config" else node.moduleConfig, section)


def _int_range(fd) -> tuple[int, int]:
    bits = 64 if fd.cpp_type in (FD.CPPTYPE_INT64, FD.CPPTYPE_UINT64) else 32
    signed = fd.cpp_type in (FD.CPPTYPE_INT32, FD.CPPTYPE_INT64)
    return (-(2 ** (bits - 1)), 2 ** (bits - 1) - 1) if signed else (0, 2 ** bits - 1)


def _is_repeated(fd) -> bool:
    flag = getattr(fd, "is_repeated", None)  # newer protobuf has it; older has only label
    return flag if flag is not None else fd.label == FD.LABEL_REPEATED


def _kind(fd) -> str | None:
    """bool, enum, string, float or int; None when a field cannot be edited here (lists, nested messages, raw bytes such as keys)."""
    if _is_repeated(fd) or fd.cpp_type == FD.CPPTYPE_MESSAGE or fd.type == FD.TYPE_BYTES:
        return None
    return {FD.CPPTYPE_BOOL: "bool", FD.CPPTYPE_ENUM: "enum", FD.CPPTYPE_STRING: "string",
            FD.CPPTYPE_FLOAT: "float", FD.CPPTYPE_DOUBLE: "float"}.get(fd.cpp_type, "int")


def describe(msg, section: str) -> list[dict]:
    """The editable fields of one section, for the form. Hidden and non-editable fields are left out, so they are absent from backups too."""
    out = []
    for fd in msg.DESCRIPTOR.fields:
        kind = _kind(fd)
        if kind is None or (section, fd.name) in HIDDEN_FIELDS:
            continue
        f = {"name": fd.name, "label": pretty(fd.name), "kind": kind, "value": getattr(msg, fd.name)}
        if kind == "enum":
            f["choices"] = [v.name for v in fd.enum_type.values]
            f["value"] = fd.enum_type.values_by_number[f["value"]].name if f["value"] in fd.enum_type.values_by_number else f["value"]
        elif kind == "int":
            f["min"], f["max"] = LIMITS.get((section, fd.name)) or _int_range(fd)
        elif kind == "string":
            f["max"] = MAX_STRING
        if (section, fd.name) in HELP:
            f["help"] = HELP[(section, fd.name)]
        if (section, fd.name) in RISKS:
            f["risk"] = RISKS[(section, fd.name)]
        out.append(f)
    return out


def snapshot(node) -> dict[str, dict]:
    """Every editable value of every allowed section: {section: {field: value}}."""
    return {section: {f["name"]: f["value"] for f in describe(_message(node, section), section)} for section in SECTIONS}


def _coerce(fd, value, section: str):
    """Check `value` against the field's type and range and return it in protobuf form. Strict: JSON true is not the number 1."""
    kind, name = _kind(fd), fd.name
    if kind == "bool":
        if not isinstance(value, bool):
            raise ConfigError(f"{pretty(name)} must be on or off.")
        return value
    if kind == "enum":
        names = {v.name: v.number for v in fd.enum_type.values}
        if isinstance(value, str) and value in names:
            return names[value]
        raise ConfigError(f"{pretty(name)}: '{str(value)[:30]}' isn't one of the choices.")
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value != int(value):
            raise ConfigError(f"{pretty(name)} must be a whole number.")
        lo, hi = LIMITS.get((section, name)) or _int_range(fd)
        if not lo <= value <= hi:
            raise ConfigError(f"{pretty(name)} must be between {lo} and {hi}.")
        return int(value)
    if kind == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ConfigError(f"{pretty(name)} must be a number.")
        return struct.unpack("f", struct.pack("f", value))[0] if fd.cpp_type == FD.CPPTYPE_FLOAT else float(value)
    if not isinstance(value, str) or len(value) > MAX_STRING or any(not ch.isprintable() for ch in value):
        raise ConfigError(f"{pretty(name)} must be plain text of at most {MAX_STRING} characters.")
    return value


def plan(node, changes) -> list[tuple]:
    """Validate {section: {field: value}} against the radio's current settings. Returns [(section, candidate message, [changes])] for the
    sections that really change; raises ConfigError before anything is touched when any part is invalid."""
    if not isinstance(changes, dict) or not changes:
        raise ConfigError("There is nothing to change.")
    out = []
    for section, fields in changes.items():
        if not isinstance(section, str) or not isinstance(fields, dict):
            raise ConfigError("Malformed request.")
        current = _message(node, section)
        cand = type(current)()
        cand.CopyFrom(current)  # work on a copy; the live message is only touched when writing
        diffs = []
        for name, value in fields.items():
            fd = current.DESCRIPTOR.fields_by_name.get(name) if isinstance(name, str) else None
            if fd is None or _kind(fd) is None or (section, name) in HIDDEN_FIELDS:
                raise ConfigError(f"'{str(name)[:40]}' isn't a setting you can change in {SECTIONS[section][1]}.")
            new, old = _coerce(fd, value, section), getattr(current, name)
            if new == old:
                continue
            setattr(cand, name, new)
            view = (lambda v, fd=fd: fd.enum_type.values_by_number[v].name) if fd.cpp_type == FD.CPPTYPE_ENUM else (lambda v: v)
            diffs.append({"section": section, "field": name, "label": pretty(name), "old": view(old), "new": view(new), "risk": RISKS.get((section, name))})
        if diffs:
            out.append((section, cand, diffs))
    return out


def _number(value, low: float, high: float, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ConfigError(f"{what} must be a number between {low:g} and {high:g}.")
    return float(value)


def check_name(text, longest: int, what: str, *, by_bytes: bool) -> str:
    if not isinstance(text, str):
        raise ConfigError(f"{what} must be text.")
    text = text.strip()
    size = len(text.encode("utf-8")) if by_bytes else len(text)
    if not text or size > longest or any(unicodedata.category(ch)[0] == "C" for ch in text):
        raise ConfigError(f"{what} must be 1 to {longest} {'bytes' if by_bytes else 'characters'} of plain text.")
    return text


class RadioConfig:
    """The operator-facing side: the form, copies of the settings, and validated writes to the connected radio.

    `node` returns the library's local node (raising ConfigError when no radio is connected); `enabled` says whether the operator has turned
    writes on. Methods are blocking (they talk to the radio): the API runs them in a worker thread."""

    def __init__(self, *, node: Callable[[], Any], store: Store, radio_id: Callable[[], str | None], enabled: Callable[[], bool]) -> None:
        self._node, self.store, self._radio_id, self._enabled = node, store, radio_id, enabled
        self._busy = threading.Lock()

    def _writable(self) -> None:
        if not self._enabled():
            raise ConfigError("Changing the radio's settings is turned off (Settings, Radio settings).", 403)

    def _node_or_error(self):
        try:
            return self._node()
        except ConfigError:
            raise
        except Exception as exc:
            raise ConfigError(str(exc) or "The radio isn't connected.", 409) from exc

    # ---- reading ---------------------------------------------------------------------------------------------------------------
    def view(self) -> dict:
        """The form for each section, the saved copies, and whether changes are allowed."""
        out = {"connected": False, "writable": self._enabled(), "sections": [], "backups": self.store.radio_backups(), "radio": None,
               "excluded": [{"name": k, "why": v} for k, v in EXCLUDED.items()]}
        try:
            node = self._node()
        except Exception:
            return out
        out["connected"], out["radio"] = True, self._radio_id()
        try:
            out["sections"] = [{"name": s, "kind": kind, "label": label, "fields": describe(_message(node, s), s)} for s, (kind, label) in SECTIONS.items()]
        except ConfigError as exc:
            out["error"] = str(exc)
        return out

    def pull(self) -> dict:
        """Read the radio's settings and keep a copy (harmless: nothing is written to the radio)."""
        backup_id = self.store.add_radio_backup("pulled", self._radio_id(), snapshot(self._node_or_error()), BACKUP_KEEP)
        return {**self.view(), "backup_id": backup_id}

    # ---- writing ---------------------------------------------------------------------------------------------------------------
    def save(self, changes, reason: str = "before changes") -> dict:
        """Validate `changes`, keep a copy of the current settings, then write the changed sections in one settings transaction. On failure
        the in-memory copy is rolled back. The radio restarts afterwards; LoRaPedia reconnects by itself."""
        self._writable()
        node = self._node_or_error()
        steps = plan(node, changes)  # raises before anything is touched
        if not steps:
            return {"changed": [], "message": "Nothing changed: those are already the radio's values."}
        if not self._busy.acquire(blocking=False):
            raise ConfigError("The radio is busy with another settings change; try again in a moment.", 409)
        old: dict = {}
        try:
            backup_id = self.store.add_radio_backup(reason, self._radio_id(), snapshot(node), BACKUP_KEEP)  # always keep what we are about to replace
            for section, cand, _ in steps:
                live = _message(node, section)
                old[section] = type(live)()
                old[section].CopyFrom(live)
                live.CopyFrom(cand)
            try:
                node.beginSettingsTransaction()
                for section, _, _ in steps:
                    node.writeConfig(section)
                node.commitSettingsTransaction()
            except BaseException as exc:  # the library may call sys.exit on some failures
                if isinstance(exc, KeyboardInterrupt):
                    raise
                for section, prev in old.items():  # the radio didn't take it: don't pretend we did
                    _message(node, section).CopyFrom(prev)
                raise ConfigError(f"The radio didn't accept the change ({exc.__class__.__name__}).", 502) from exc
        finally:
            self._busy.release()
        return {"changed": [d for _, _, diffs in steps for d in diffs], "backup_id": backup_id, "restarting": True,
                "message": "Saved to the radio. It restarts to apply the change, and LoRaPedia reconnects by itself in a few seconds."}

    def restore(self, backup_id: int, force: bool = False) -> dict:
        """Apply an earlier copy through save(). A copy from a different radio is refused unless `force`."""
        self._writable()
        row = self.store.radio_backup(backup_id)
        if not row:
            raise ConfigError("That copy doesn't exist.", 404)
        here = self._radio_id()
        if not force and row["radio"] and here and row["radio"] != here:
            raise ConfigError(f"This copy was taken from a different radio ({row['radio']}); the one connected now is {here}. Restoring it would copy "
                              "that radio's LoRa, role and other settings onto this one.", 409)
        changes = {s: f for s, f in row["config"].items() if s in SECTIONS and isinstance(f, dict)}  # ignore anything else a file may hold
        if not changes or not plan(self._node_or_error(), changes):
            return {"changed": [], "message": "The radio already has those settings."}
        return self.save(changes, reason="before restore")

    def set_names(self, long_name, short_name) -> dict:
        self._writable()
        long_name = check_name(long_name, LONG_NAME_MAX_BYTES, "The long name", by_bytes=True)
        short_name = check_name(short_name, SHORT_NAME_MAX, "The short name", by_bytes=False)
        node = self._node_or_error()
        try:
            node.setOwner(long_name, short_name)
        except BaseException as exc:
            if isinstance(exc, KeyboardInterrupt):
                raise
            raise ConfigError(f"The radio didn't accept the name ({exc.__class__.__name__}).", 502) from exc
        return {"long_name": long_name, "short_name": short_name, "message": "Name sent to the radio. It restarts to apply it."}

    def set_position(self, lat, lon, alt) -> dict:
        """Tell the radio where it is (a radio with no GPS has no position). The position is then shared with the whole mesh."""
        self._writable()
        lat, lon = _number(lat, -90, 90, "Latitude"), _number(lon, -180, 180, "Longitude")
        alt = int(_number(alt if alt is not None else 0, -1000, 9000, "Altitude (metres)"))
        node = self._node_or_error()
        try:
            node.setFixedPosition(lat, lon, alt)
        except BaseException as exc:
            if isinstance(exc, KeyboardInterrupt):
                raise
            raise ConfigError(f"The radio didn't accept the position ({exc.__class__.__name__}).", 502) from exc
        return {"lat": lat, "lon": lon, "alt": alt, "message": "Fixed position sent to the radio. It will share it with the mesh."}

    def clear_position(self) -> dict:
        self._writable()
        node = self._node_or_error()
        try:
            node.removeFixedPosition()
        except BaseException as exc:
            if isinstance(exc, KeyboardInterrupt):
                raise
            raise ConfigError(f"The radio didn't accept that ({exc.__class__.__name__}).", 502) from exc
        return {"message": "Fixed position removed."}
