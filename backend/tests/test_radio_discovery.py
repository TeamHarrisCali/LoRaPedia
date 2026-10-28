"""Finding a USB radio automatically inside Docker: the device-directory scan and the entrypoint that gives the app the device's group."""

import os
import subprocess
from pathlib import Path

import pytest

from lorapedia.radio import meshtastic_radio as MR

ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = ROOT / "docker" / "entrypoint.sh"


# ---- the entrypoint ------------------------------------------------------------------------------------------------------


def entrypoint(args=("lorapedia",), dev=None, extra=None, dry=True):
    env = {"PATH": os.environ["PATH"], **({"LORAPEDIA_ENTRYPOINT_DRY_RUN": "1"} if dry else {})}
    if dev is not None:
        env["LORAPEDIA_DEVICE_DIR"] = str(dev)
    if extra is not None:
        env["LORAPEDIA_EXTRA_GIDS"] = extra
    return subprocess.run(["sh", str(ENTRYPOINT), *args], capture_output=True, text=True, env=env)


def device_dir(tmp_path, names, target="/dev/tty"):
    d = tmp_path / "dev"
    d.mkdir(exist_ok=True)
    for n in names:
        (d / n).symlink_to(target)  # a character device, whose group the script reads (it follows the link, as it would a real node)
    return d


TTY_GID = os.stat("/dev/tty").st_gid if os.path.exists("/dev/tty") else 0
needs_group = pytest.mark.skipif(TTY_GID == 0, reason="needs a character device with a non-root group")
PREFIX = "setpriv --reuid=10001 --regid=10001"
TAIL = "--bounding-set=-all --inh-caps=-all --no-new-privs -- lorapedia"


@needs_group
def test_the_app_gets_the_group_of_every_serial_device_it_can_see(tmp_path):
    d = device_dir(tmp_path, ["ttyACM0", "ttyUSB0", "ttyS0"])
    r = entrypoint(dev=d)
    assert r.returncode == 0 and r.stdout.strip() == f"{PREFIX} --groups={TTY_GID} {TAIL}"  # three devices, one group, listed once


def test_with_no_serial_device_it_still_drops_to_the_unprivileged_user_with_no_groups(tmp_path):
    r = entrypoint(dev=device_dir(tmp_path, []))
    assert r.stdout.strip() == f"{PREFIX} --clear-groups {TAIL}"


@needs_group
def test_only_serial_style_character_devices_count(tmp_path):
    d = device_dir(tmp_path, ["tty0", "tty1", "tty", "console"])  # the virtual consoles, the controlling terminal, and others
    (d / "ttyACM7").write_text("a regular file, not a device")
    assert entrypoint(dev=d).stdout.strip() == f"{PREFIX} --clear-groups {TAIL}"


def test_extra_groups_are_validated_deduplicated_and_never_root(tmp_path):
    d = device_dir(tmp_path, [])
    r = entrypoint(dev=d, extra="20,abc,0,986,20,-5,9999999999999999999999")
    assert "--groups=20,986,9999999999999999999999 " in r.stdout and " 0," not in r.stdout and "abc" not in r.stdout
    assert "--groups=0" not in entrypoint(dev=d, extra="0").stdout


def test_at_most_sixteen_groups_are_granted(tmp_path):
    r = entrypoint(dev=device_dir(tmp_path, []), extra=",".join(str(g) for g in range(100, 130)))
    groups = r.stdout.split("--groups=")[1].split(" ")[0].split(",")
    assert len(groups) == 16


def test_every_capability_is_removed_and_no_new_ones_can_be_gained(tmp_path):
    out = entrypoint(dev=device_dir(tmp_path, [])).stdout
    for flag in ("--bounding-set=-all", "--inh-caps=-all", "--no-new-privs", "--reuid=10001", "--regid=10001"):
        assert flag in out


def test_the_command_and_its_arguments_are_passed_through_unchanged(tmp_path):
    out = entrypoint(args=("lorapedia", "--port", "9000"), dev=device_dir(tmp_path, [])).stdout.strip()
    assert out.endswith("-- lorapedia --port 9000")


@pytest.mark.skipif(os.getuid() == 0, reason="as root the script would try to change identity")
def test_started_as_an_ordinary_user_it_just_runs_the_command(tmp_path):
    r = entrypoint(args=("echo", "hello world"), dev=device_dir(tmp_path, []), dry=False)
    assert r.returncode == 0 and r.stdout == "hello world\n"  # an older compose file, or docker run: nothing to change


# ---- finding the radio ---------------------------------------------------------------------------------------------------


def fake_system(tmp_path, devices):
    """A device directory and a sysfs with the given {name: vendor id or None}. Every device file is a symlink to /dev/null."""
    dev, sysfs = tmp_path / "hostdev", tmp_path / "sys" / "class" / "tty"
    dev.mkdir(parents=True, exist_ok=True)
    sysfs.mkdir(parents=True, exist_ok=True)
    for name, vid in devices.items():
        (dev / name).symlink_to("/dev/null")
        if vid is not None:
            usb = tmp_path / "usb" / name
            (usb / "if00").mkdir(parents=True)
            (usb / "idVendor").write_text(f"{vid:04x}\n")  # the id sits on the USB device, one level above the interface
            (sysfs / name).mkdir()
            (sysfs / name / "device").symlink_to(usb / "if00")
    return str(dev), str(sysfs)


def vids():
    from meshtastic.util import blacklistVids, whitelistVids
    return sorted(whitelistVids)[0], sorted(blacklistVids)[0]


def test_the_vendor_is_read_from_sysfs_and_unknown_when_it_cannot_be(tmp_path):
    good, _ = vids()
    dev, sysfs = fake_system(tmp_path, {"ttyACM0": good, "ttyACM1": None})
    assert MR._usb_vendor("ttyACM0", sysfs) == good and MR._usb_vendor("ttyACM1", sysfs) is None and MR._usb_vendor("nope", sysfs) is None


def test_a_radio_from_a_vendor_meshtastic_lists_wins_over_unknown_devices(tmp_path):
    good, bad = vids()
    dev, sysfs = fake_system(tmp_path, {"ttyACM0": good, "ttyACM1": bad, "ttyUSB0": None})
    assert MR.scan_device_dir(dev, sysfs) == [os.path.join(dev, "ttyACM0")]


def test_without_a_listed_vendor_anything_not_known_to_be_something_else_is_offered(tmp_path):
    _, bad = vids()
    dev, sysfs = fake_system(tmp_path, {"ttyACM0": bad, "ttyUSB0": None, "ttyUSB1": None})
    assert MR.scan_device_dir(dev, sysfs) == [os.path.join(dev, "ttyUSB0"), os.path.join(dev, "ttyUSB1")]
    assert MR.scan_device_dir(str(tmp_path / "empty"), sysfs) == []  # no such directory: no radio, not an error


def test_only_character_devices_and_only_usb_serial_names_are_candidates(tmp_path):
    good, _ = vids()
    dev, sysfs = fake_system(tmp_path, {"ttyACM0": good, "ttyS0": good, "tty0": good})
    (Path(dev) / "ttyACM5").write_text("not a device")
    assert MR.scan_device_dir(dev, sysfs) == [os.path.join(dev, "ttyACM0")]


def test_a_radio_that_comes_back_under_another_number_is_found_again(tmp_path):
    good, _ = vids()
    dev, sysfs = fake_system(tmp_path, {"ttyACM0": good})
    assert MR.scan_device_dir(dev, sysfs) == [os.path.join(dev, "ttyACM0")]
    (Path(dev) / "ttyACM0").unlink()                      # unplugged, or it reset
    assert MR.scan_device_dir(dev, sysfs) == []
    fake_system(tmp_path, {"ttyACM1": good})              # and returns as ACM1 (several radios do this)
    assert MR.scan_device_dir(dev, sysfs) == [os.path.join(dev, "ttyACM1")]


def test_the_environment_variable_chooses_between_the_normal_finder_and_the_device_directory(tmp_path, monkeypatch):
    good, _ = vids()
    dev, sysfs = fake_system(tmp_path, {"ttyUSB0": None})
    monkeypatch.setattr("meshtastic.util.findPorts", lambda eliminate_duplicates=False: ["/dev/ttyACM9"])
    monkeypatch.delenv(MR.DEVICE_DIR_ENV, raising=False)
    assert MR.list_serial_ports() == ["/dev/ttyACM9"]
    monkeypatch.setenv(MR.DEVICE_DIR_ENV, "/dev")
    assert MR.list_serial_ports() == ["/dev/ttyACM9"]
    monkeypatch.setenv(MR.DEVICE_DIR_ENV, dev)
    assert MR.list_serial_ports() == [os.path.join(dev, "ttyUSB0")]


def test_a_permission_error_in_docker_explains_the_group_and_the_restart(monkeypatch):
    spec = MR.ConnectionSpec("serial", "/hostdev/ttyACM0")
    monkeypatch.delenv(MR.DEVICE_DIR_ENV, raising=False)
    assert "dialout" in MR._friendly(PermissionError(13, "Permission denied"), spec).args[0]
    monkeypatch.setenv(MR.DEVICE_DIR_ENV, "/hostdev")
    msg = MR._friendly(PermissionError(13, "Permission denied"), spec).args[0]
    assert "docker compose restart lorapedia" in msg and "dialout" not in msg
