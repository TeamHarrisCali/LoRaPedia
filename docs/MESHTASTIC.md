# Setting up the Meshtastic radio

LoRaPedia talks to a normal Meshtastic device. You do not need special firmware. This guide covers flashing,
configuring, and connecting it. For anything about Meshtastic itself, the
[official documentation](https://meshtastic.org/docs/) is the authority; this page reflects it at the time of
writing and may drift.

## 1. Flash the firmware

1. Open the [Meshtastic web flasher](https://flasher.meshtastic.org) in Chrome or Edge (they can talk to USB
   devices; Firefox and Safari cannot).
2. Plug the device in with a **data** USB cable. If the browser cannot see the device, try another cable: many
   are charge-only.
3. Pick your device model and the latest stable firmware, and follow the prompts.

A device that already runs current Meshtastic firmware can skip this.

## 2. Configure it

Use the Meshtastic phone app, the web client, or the CLI. The settings that matter:

| Setting | Why |
|---|---|
| **LoRa region** | **Required.** The radio will not transmit until you set the region for your country. |
| **Device name** | A short, memorable name such as `WIKI`. It is what people will message. |
| **Channel** | Use the same channel settings (name and key) as the rest of your mesh, or people will not be able to reach it. |
| **Role** | `CLIENT` is fine. |
| **WiFi** *(optional)* | Only if you want to connect over the network instead of USB. Enable it and note the IP address. |

A radio dedicated to LoRaPedia is best. Do not also pair a phone app to it over Bluetooth while LoRaPedia is
using it; only one program should talk to the radio at a time.

## 3. Connect it to LoRaPedia

### USB (the usual way)

1. Plug the radio into the computer that runs LoRaPedia.
2. **Linux:** your user needs permission to use serial ports. Add yourself to the right group, then log out and
   back in:
   ```bash
   sudo usermod -aG dialout $USER     # Debian/Ubuntu/Raspberry Pi OS
   sudo usermod -aG uucp $USER        # Arch
   ```
   If the port is "busy", ModemManager may have grabbed it: `sudo systemctl stop ModemManager`.
3. In LoRaPedia open **Radio**, choose **Automatic (USB)**, press **Connect**. If you have several radios,
   choose **USB, choose the port** and pick one (typically `/dev/ttyACM0` or `/dev/ttyUSB0`).

You should see the device's name and node ID appear, and the status dot in the top bar turn teal.

**Docker:** pass the device through and grant access. See the commented USB section of
[docker-compose.yml](../docker-compose.yml): uncomment `devices:` and `group_add:` and use the numeric group id
from `stat -c %g /dev/ttyACM0`.

### Network (WiFi / Ethernet)

Choose **Network** and enter the radio's IP address or hostname. The radio listens on TCP port 4403 for this.
This works in Docker too.

### Bluetooth

Choose **Bluetooth**, press **Scan** with the radio nearby, and pick it. Bluetooth does not work inside Docker;
use `./run.sh`.

## 4. Check that it works

1. On a **second** Meshtastic device (your phone is fine), open a **direct message** to the LoRaPedia node.
2. Send a question: `what is a quasar?`
3. Watch **Live Feed** in LoRaPedia: the question appears, then the answer.

To check that the radio can transmit at all, without involving a model, send a direct message to a node it has
heard (it refuses broadcasts and unknown nodes):

```bash
curl -X POST localhost:8000/api/radio/test-send -H 'content-type: application/json' \
  -d '{"to": "!a1b2c3d4", "text": "hello from LoRaPedia"}'
```

If nothing arrives, see the troubleshooting table in the [README](../README.md#troubleshooting). The most common
reasons: the question was sent to a channel while the node is in direct-message mode, or the two devices are
on different channel keys.

## Good manners on a shared mesh

LoRa airtime is a shared, slow resource. LoRaPedia is deliberately polite by default: one question per node per
minute, one reply at a time, a pause between messages, and short answers. If your mesh is busy, lower the hop
limit, keep multi-part answers to 2 or 3 parts, and consider "send part 1, rest on `?more`".
