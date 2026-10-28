"""Plain-language help for a USB radio that will not connect, on Linux.

The usual causes are mundane and differ from computer to computer: the cable carries no data, the radio's device file belongs to a
group the program is not in, another program (ModemManager, the Meshtastic app or CLI) has the port open, or, in Docker, the container
learned the device's group before the radio was plugged in. This looks at what is actually there (the device files, their owner group
and permissions, who we are) and says which of these it is and what to type. Pure decisions over injected facts, so every case is tested.
"""
import os
import re
import stat
import sys
from dataclasses import dataclass
from typing import Callable

DEVICE_RE = re.compile(r"^tty(?:ACM|USB)\d+$")
SAFE_WORD = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")  # a group name is only ever put into a command if it is this plain
DEVICE_DIR_ENV = "LORAPEDIA_DEVICE_DIR"


@dataclass(frozen=True)
class Device:
    name: str          # ttyACM0
    gid: int           # the group that owns the device file
    mode: int          # permission bits, e.g. 0o660
    usable: bool       # can this process open it for reading and writing right now?
    vid: int | None    # the USB vendor id, when it can be read


def probe_devices(root: str) -> list[Device]:
    """The USB serial device files in `root` (the container's /hostdev, or /dev)."""
    from .meshtastic_radio import _usb_vendor  # local import: that module is the one that knows how to read sysfs
    out = []
    try:
        names = sorted(n for n in os.listdir(root) if DEVICE_RE.match(n))
    except OSError:
        return []
    for name in names:
        path = os.path.join(root, name)
        try:
            st = os.stat(path)
        except OSError:
            continue
        if stat.S_ISCHR(st.st_mode):
            out.append(Device(name, st.st_gid, stat.S_IMODE(st.st_mode), os.access(path, os.R_OK | os.W_OK), _usb_vendor(name)))
    return out


def _group_name(gid: int) -> str | None:
    try:
        import grp
        name = grp.getgrgid(gid).gr_name
        return name if SAFE_WORD.match(name) else None
    except (ImportError, KeyError):
        return None


def _cmd(label: str, text: str) -> dict:
    return {"label": label, "text": text}


SERIAL_GROUPS = ("dialout", "uucp", "plugdev")  # what Debian/Ubuntu/Fedora, Arch and some others call the group for serial devices


def _group_exists(name: str) -> bool:
    try:
        import grp
        grp.getgrnam(name)
        return True
    except (ImportError, KeyError):
        return False


def serial_group(exists: Callable[[str], bool] = _group_exists) -> str:
    """The group a udev rule should hand radios to: the first of the usual names that exists on this computer, else 'dialout'."""
    return next((g for g in SERIAL_GROUPS if exists(g)), "dialout")


def _udev_rule(d: Device, group: str = "dialout") -> str:
    match = f'ATTRS{{idVendor}}=="{d.vid:04x}", ' if d.vid is not None else ""
    return f'SUBSYSTEM=="tty", {match}MODE="0660", GROUP="{group}"'


def radio_help(*, method: str, error: str | None, environ: dict | None = None, platform: str | None = None,
               probe: Callable[[str], list[Device]] = probe_devices, groups: Callable[[], list[int]] = os.getgroups,
               group_name: Callable[[int], str | None] = _group_name,
               group_exists: Callable[[str], bool] = _group_exists) -> dict | None:
    """What to try, as {title, why, steps: [...], commands: [{label, text}], docker, open}, or None when there is nothing to say.

    Only for Linux and only when a USB radio is wanted (automatic or a chosen serial port). `open` says whether the problem is
    concrete enough that the box should start unfolded."""
    env = os.environ if environ is None else environ
    if (sys.platform if platform is None else platform) != "linux" or method not in ("auto", "serial"):
        return None
    docker = env.get("LORAPEDIA_INSTALL") == "docker"
    root = env.get(DEVICE_DIR_ENV) or "/dev"
    text = (error or "").lower()
    busy = any(k in text for k in ("busy", "exclusively lock", "errno 11", "errno 16"))

    if docker and not env.get(DEVICE_DIR_ENV):
        return {"docker": True, "open": True, "title": "This container cannot see USB devices",
                "why": "It was started from a docker-compose.yml that does not share the computer's device files with it.",
                "steps": ["Get the current docker-compose.yml from the project (the latest release has it), put it next to your old one, "
                          "and start LoRaPedia again. Your data is kept.",
                          "Then plug the radio in (before or after) and press Retry."],
                "commands": [_cmd("In the project folder", "git pull\ndocker compose up -d")]}

    devices = probe(root)
    host = "the computer" if docker else "this computer"
    grp_name = serial_group(group_exists) if not docker else "dialout"  # in a container the host's group names are not visible

    if not devices:
        steps = ["Check the USB cable. Many cables only charge: use one that carries data (try another).",
                 "Plug the radio straight into the computer (not through a hub) and try another port.",
                 f"See whether {host} sees it at all: the command below should list a ttyACM or ttyUSB device. If it lists nothing, "
                 "the problem is the cable, the port or the radio, not LoRaPedia.",
                 "After plugging it in, the last lines of the kernel log should name the device (and say if it disconnected again)."]
        if docker:
            steps.append("If the computer does list it but LoRaPedia does not, restart LoRaPedia so it picks the device up.")
        return {"docker": docker, "open": False, "title": "No USB radio found",
                "why": "No USB serial device is visible to LoRaPedia." + (" The simulator is used meanwhile." if method == "auto" else ""),
                "steps": steps,
                "commands": [_cmd("List serial devices", "ls -l /dev/ttyACM* /dev/ttyUSB*"), _cmd("Read the kernel log", "sudo dmesg | tail -n 15")]
                + ([_cmd("Restart LoRaPedia", "docker compose restart lorapedia")] if docker else [])}

    blocked = [d for d in devices if not d.usable]
    if blocked:
        d = blocked[0]
        dev = f"/dev/{d.name}"
        mine = groups()
        if d.gid == 0:
            return {"docker": docker, "open": True, "title": "The radio's device file is only open to root",
                    "why": f"{dev} belongs to the root group (permissions {d.mode:o}), so no ordinary program can open it, and "
                           "LoRaPedia deliberately never takes root's group. A udev rule makes the computer give such radios to the 'dialout' group.",
                    "steps": ["Create the rule file below, then reload the rules and unplug and replug the radio."]
                             + (["'dialout' is the usual group; on Arch it is 'uucp'. The last command shows which exists on your computer: "
                                 "use that name in the rule if it is not dialout.", "Then restart LoRaPedia, which learns the device's group when it starts."] if docker else
                                ["Then add yourself to the group, and log out and in again."]),
                    "commands": [_cmd("Make the rule", f"echo '{_udev_rule(d, grp_name)}' | sudo tee /etc/udev/rules.d/99-meshtastic.rules"),
                                 _cmd("Reload the rules", "sudo udevadm control --reload && sudo udevadm trigger")]
                    + ([_cmd("Which serial group exists here", "getent group dialout uucp plugdev"),
                        _cmd("Restart LoRaPedia", "docker compose restart lorapedia")] if docker else
                       [_cmd("Join the group", f"sudo usermod -aG {grp_name} $USER")]),
                    "facts": {"device": dev, "group": "root", "mode": f"{d.mode:o}"}}
        if d.gid in mine or d.gid == os.getgid():
            # We ARE in the device's group and still cannot open it: the file simply does not allow its group to write.
            return {"docker": docker, "open": True, "title": "The radio's device file does not allow its group to use it",
                    "why": f"{dev} belongs to group {d.gid}, which this program is in, but its permissions are {d.mode:o}: the group "
                           "may not write to it. A udev rule gives radios like this the permissions they need.",
                    "steps": ["Create the rule file below, reload the rules, then unplug and replug the radio."]
                             + (["Then restart LoRaPedia."] if docker else []),
                    "commands": [_cmd("Make the rule", f"echo '{_udev_rule(d, grp_name)}' | sudo tee /etc/udev/rules.d/99-meshtastic.rules"),
                                 _cmd("Reload the rules", "sudo udevadm control --reload && sudo udevadm trigger")]
                    + ([_cmd("Which serial group exists here", "getent group dialout uucp plugdev"),
                        _cmd("Restart LoRaPedia", "docker compose restart lorapedia")] if docker else []),
                    "facts": {"device": dev, "group": d.gid, "mode": f"{d.mode:o}"}}
        if docker:
            return {"docker": True, "open": True, "title": "The container does not have the radio's group",
                    "why": f"{dev} belongs to group {d.gid} (permissions {d.mode:o}). LoRaPedia's container learns that group when it "
                           "starts, so a radio plugged in afterwards, or one whose group changed, is not openable until it restarts.",
                    "steps": ["Restart LoRaPedia. It will read the radio's group again.",
                              "If that does not fix it, make sure you are using the current docker-compose.yml (the project's latest), "
                              "which starts the container so it can take on the device's group."],
                    "commands": [_cmd("Restart LoRaPedia", "docker compose restart lorapedia"),
                                 _cmd("Check the radio's group on the computer", f"ls -ln {dev}")],
                    "facts": {"device": dev, "group": d.gid, "mode": f"{d.mode:o}", "container_groups": sorted(mine)}}
        name = group_name(d.gid)
        return {"docker": False, "open": True, "title": "You are not allowed to open the radio",
                "why": f"{dev} belongs to the group '{name or d.gid}' (permissions {d.mode:o}) and your user is not in it.",
                "steps": ["Add your user to that group, then log out and log in again (a new login is what applies it).",
                          "Check afterwards with the second command: the group should be in the list."],
                "commands": [_cmd("Join the group", f"sudo usermod -aG {name} $USER") if name else
                             _cmd("Find the group's name", f"ls -l {dev}"),
                             _cmd("Check your groups", "id -nG")],
                "facts": {"device": dev, "group": name or d.gid, "mode": f"{d.mode:o}"}}

    if busy:
        return {"docker": docker, "open": True, "title": "Another program has the radio open",
                "why": "The serial port can be used by one program at a time, and something else is holding it.",
                "steps": ["Close the Meshtastic app, the Meshtastic command line tool, and any serial monitor or second LoRaPedia.",
                          "On Linux, ModemManager often grabs new serial devices. Stopping it frees the port (you can disable it "
                          "if you do not use a mobile modem).",
                          "The first command shows which program holds the port."]
                         + (["If you started a trial container or another LoRaPedia, stop it: `docker ps` lists them."] if docker else []),
                "commands": [_cmd("Who has it open", f"sudo fuser -v /dev/{devices[0].name}"),
                             _cmd("Stop ModemManager", "sudo systemctl stop ModemManager"),
                             _cmd("Keep it stopped", "sudo systemctl disable ModemManager")]
                + ([_cmd("List containers", "docker ps")] if docker else [])}

    return {"docker": docker, "open": False, "title": "The radio is there but did not answer",
            "why": "LoRaPedia can open the port but the radio did not reply to it.",
            "steps": ["Unplug the radio, wait a few seconds, and plug it in again. Then press Retry.",
                      "Make sure it is running Meshtastic firmware (not in its flashing mode) and has power.",
                      "Check the radio's own settings: the serial interface must not be switched off.",
                      "Test the radio with the official tool; if that cannot talk to it either, the problem is the radio or its firmware."],
            "commands": [_cmd("Test with the Meshtastic CLI", "meshtastic --info")]}
