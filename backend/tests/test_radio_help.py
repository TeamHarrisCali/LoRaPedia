"""Help for a USB radio that will not connect (Linux): each cause is told apart from the facts on the computer."""

import os
import pwd  # noqa: F401  (Linux only: the suite is run on Linux)

import pytest

from lorapedia.radio.help import Device, probe_devices, radio_help, serial_group

DOCKER = {"LORAPEDIA_INSTALL": "docker", "LORAPEDIA_DEVICE_DIR": "/hostdev"}
PLAIN = {}


def dev(name="ttyACM0", gid=950, mode=0o660, usable=True, vid=0x239A):
    return Device(name, gid, mode, usable, vid)


def help_for(devices=(), env=PLAIN, error="No radio found. Is it plugged in?", method="auto", groups=(), gname=lambda g: "plugdev",
             exists=lambda g: g == "dialout"):
    return radio_help(method=method, error=error, environ=env, platform="linux", probe=lambda root: list(devices),
                      groups=lambda: list(groups), group_name=gname, group_exists=exists)


def commands(h):
    return "\n".join(c["text"] for c in h["commands"])


def test_only_linux_and_only_when_a_usb_radio_is_wanted():
    assert radio_help(method="auto", error="x", environ=PLAIN, platform="win32", probe=lambda r: []) is None
    assert radio_help(method="auto", error="x", environ=PLAIN, platform="darwin", probe=lambda r: []) is None
    for method in ("tcp", "ble", "simulator"):
        assert help_for(method=method) is None
    assert help_for(method="serial")["title"] == "No USB radio found"


def test_no_device_visible_says_check_the_cable_and_how_to_see_what_the_computer_sees():
    h = help_for([])
    assert h["title"] == "No USB radio found" and h["open"] is False and "carries data" in h["steps"][0]
    assert "ls -l /dev/ttyACM* /dev/ttyUSB*" in commands(h) and "dmesg" in commands(h) and "restart" not in commands(h).lower()


def test_no_device_visible_in_docker_also_offers_a_restart():
    assert "docker compose restart lorapedia" in commands(help_for([], env=DOCKER))


def test_a_container_without_the_device_share_is_told_to_use_the_current_compose_file():
    h = help_for([], env={"LORAPEDIA_INSTALL": "docker"})
    assert h["title"] == "This container cannot see USB devices" and h["open"] and "docker compose up -d" in commands(h)


def test_not_in_the_devices_group_names_the_group_and_the_command_to_join_it():
    h = help_for([dev(usable=False, gid=20)], gname=lambda g: "dialout")
    assert h["title"] == "You are not allowed to open the radio" and h["open"]
    assert "sudo usermod -aG dialout $USER" in commands(h) and "log out" in " ".join(h["steps"]).lower()
    assert h["facts"]["group"] == "dialout" and h["facts"]["device"] == "/dev/ttyACM0"


def test_a_group_whose_name_is_not_plain_is_never_put_into_a_command():
    h = help_for([dev(usable=False, gid=20)], gname=lambda g: None)
    assert "usermod" not in commands(h) and "ls -l /dev/ttyACM0" in commands(h)


def test_a_device_only_root_can_open_gets_a_udev_rule_for_that_vendor_and_never_roots_group():
    h = help_for([dev(usable=False, gid=0, mode=0o600, vid=0x239A)])
    assert h["title"] == "The radio's device file is only open to root"
    assert 'ATTRS{idVendor}=="239a"' in commands(h) and "99-meshtastic.rules" in commands(h) and "udevadm" in commands(h)
    assert "usermod -aG dialout" in commands(h)
    no_vid = help_for([dev(usable=False, gid=0, vid=None)])
    assert "idVendor" not in commands(no_vid) and 'SUBSYSTEM=="tty"' in commands(no_vid)


def test_in_docker_a_group_the_container_lacks_means_restart_so_it_is_learned_again():
    h = help_for([dev(usable=False, gid=950)], env=DOCKER, groups=[10001])
    assert h["title"] == "The container does not have the radio's group" and h["open"]
    assert "docker compose restart lorapedia" in commands(h) and "ls -ln /dev/ttyACM0" in commands(h)
    assert h["facts"]["group"] == 950 and h["facts"]["container_groups"] == [10001]


def test_in_docker_a_root_only_device_gets_the_udev_rule_then_a_restart():
    h = help_for([dev(usable=False, gid=0)], env=DOCKER)
    assert "udevadm" in commands(h) and "docker compose restart lorapedia" in commands(h) and "usermod" not in commands(h)


@pytest.mark.parametrize("error", ["Could not connect to the radio: [Errno 11] Could not exclusively lock port /dev/ttyACM0",
                                   "The serial port is busy. Close other apps", "[Errno 16] Device or resource busy"])
def test_a_port_that_is_open_but_busy_points_at_the_other_program(error):
    h = help_for([dev()], error=error)
    assert h["title"] == "Another program has the radio open" and "sudo fuser -v /dev/ttyACM0" in commands(h)
    assert "systemctl stop ModemManager" in commands(h) and "docker ps" not in commands(h)
    assert "docker ps" in commands(help_for([dev()], error=error, env=DOCKER))


def test_a_usable_device_that_does_not_answer_gets_the_replug_and_firmware_checklist():
    h = help_for([dev()], error="Could not connect to the radio: Timed out waiting for connection")
    assert h["title"] == "The radio is there but did not answer" and "meshtastic --info" in commands(h) and h["open"] is False


def test_only_the_first_blocked_device_is_explained_and_names_in_commands_are_device_file_names():
    h = help_for([dev("ttyACM0", usable=True), dev("ttyUSB1", gid=20, usable=False)], gname=lambda g: "dialout")
    assert h["facts"]["device"] == "/dev/ttyUSB1" and "ttyACM0" not in commands(h)


def test_probe_reads_real_device_files_and_ignores_everything_else(tmp_path):
    (tmp_path / "ttyACM0").symlink_to("/dev/null")        # a character device
    (tmp_path / "ttyS0").symlink_to("/dev/null")          # not USB serial
    (tmp_path / "ttyACMx").symlink_to("/dev/null")        # not a number
    (tmp_path / "ttyUSB3").write_text("a regular file")   # not a device
    found = probe_devices(str(tmp_path))
    assert [d.name for d in found] == ["ttyACM0"] and found[0].usable is True and isinstance(found[0].gid, int)
    assert probe_devices(str(tmp_path / "missing")) == []


def test_the_radio_status_carries_the_help_only_while_there_is_no_radio():
    import asyncio

    from lorapedia.config import Settings
    from lorapedia.radio.manager import RadioManager

    mgr = RadioManager(settings=lambda: Settings(radio_method="auto"), submit=lambda p: asyncio.sleep(0), port_lister=lambda: [])
    mgr.error = "No radio found. Is it plugged in?"
    st = mgr.status()
    assert "help" in st and (st["help"] is None or st["help"]["title"])  # Linux CI: a title; other systems: None
    assert RadioManager(settings=lambda: Settings(radio_method="simulator"), submit=lambda p: asyncio.sleep(0),
                        port_lister=lambda: []).status()["help"] is None


def test_being_in_the_devices_group_but_still_unable_to_open_it_means_the_file_mode_is_wrong():
    """Found by running it for real: /dev/kmsg is group plugdev with mode 644, and the user is in plugdev. Saying "join the group"
    there would be wrong; the group simply may not write."""
    for env in (PLAIN, DOCKER):
        h = help_for([dev(usable=False, gid=950, mode=0o644)], env=env, groups=[10001, 950])
        assert h["title"] == "The radio's device file does not allow its group to use it" and "usermod" not in commands(h)
        assert "MODE=\"0660\"" in commands(h) and "644" in h["why"]


def test_the_udev_rule_hands_radios_to_the_serial_group_this_computer_actually_has():
    assert serial_group(lambda g: g == "uucp") == "uucp" and serial_group(lambda g: g in ("dialout", "uucp")) == "dialout"
    assert serial_group(lambda g: g == "plugdev") == "plugdev" and serial_group(lambda g: False) == "dialout"
    on_arch = help_for([dev(usable=False, gid=0)], exists=lambda g: g == "uucp")
    assert 'GROUP="uucp"' in commands(on_arch) and "usermod -aG uucp $USER" in commands(on_arch)


def test_in_docker_the_hosts_group_names_are_unknown_so_the_user_is_shown_how_to_look_them_up():
    h = help_for([dev(usable=False, gid=0)], env=DOCKER)
    assert 'GROUP="dialout"' in commands(h) and "getent group dialout uucp plugdev" in commands(h) and "uucp" in " ".join(h["steps"])
