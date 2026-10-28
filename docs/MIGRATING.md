# Moving from `./run.sh` to Docker

Docker is the supported way to run LoRaPedia on Linux, because it is what lets it **update itself safely** (see
[UPDATING.md](UPDATING.md)) and come back by itself after a reboot. This moves an existing `./run.sh` install, with its Wikipedia
index, settings and question log, into Docker. **Your old data folder is copied, never changed**, so going back is just `./run.sh`.

## Before you start (once)

```bash
sudo systemctl enable --now docker      # so the node comes back after a reboot (the socket alone does not start containers)
```

If you have an NVIDIA GPU and want Ollama to use it from a container:

```bash
# Arch:     sudo pacman -S nvidia-container-toolkit
# Debian/Ubuntu: see https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html
sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker
docker run --rm --gpus all python:3.12-slim-bookworm nvidia-smi     # should list your GPU
```

Without it everything still works, on the CPU: answers take longer and indexing is several times slower.

## 1. Stop the old node and copy its data

```bash
# stop ./run.sh first (Ctrl+C in its terminal): the copy must be consistent
scripts/migrate-to-docker.sh
```

This copies `./data` into the volume Docker Compose will use, then fixes the two things that point at the old machine: the
**library's file paths** (they were absolute host paths) and any saved **`localhost` Ollama address** (inside a container,
`localhost` is the container itself; a deliberately remote Ollama is left alone). It refuses if a server is still running on the
source or if the volume already holds data. `--project NAME` puts it under another Compose project name (handy for a trial).

## 2. Add the GPU and the radio to `docker-compose.yml`

In the `ollama` service, uncomment the `deploy:` GPU block (see the file). To shrink the model's memory use, add
`environment: {OLLAMA_CONTEXT_LENGTH: "4096"}`: Ollama's default context made `llama3.2:3b` occupy about 4 GB of VRAM here, and
LoRaPedia's prompts are far smaller than that.

For a **USB radio**, map it by its stable name and give the container its group (see the USB block in the compose file):

```bash
ls /dev/serial/by-id/                    # e.g. usb-Adafruit_WisCore_RAK4631_Board_XXXX-if00
stat -c %g /dev/ttyACM0                  # the numeric group of the device (uucp on Arch, dialout on Debian/Ubuntu)
```
```yaml
    devices:
      - /dev/serial/by-id/usb-...:/dev/ttyACM0
    group_add:
      - "984"
```

## 3. Start it

```bash
docker compose up -d
docker compose exec ollama ollama pull llama3.2:3b          # the models are not copied: pull them once (about 2.3 GB with the next one)
docker compose exec ollama ollama pull nomic-embed-text
```

Open http://localhost:8000. The library, the index, the question log and your settings are all there.

## Going back

Stop the stack (`docker compose down`, which keeps the volume) and run `./run.sh` again: your original `./data` was never touched.
Keep it for a while after the move. When you are happy, you can archive it.

## Backing up the Docker volume

```bash
docker run --rm -v lorapedia_lorapedia-data:/data -v "$PWD":/backup python:3.12-slim-bookworm \
  tar czf /backup/lorapedia-data-backup.tgz -C /data .
```
(The volume name is `<compose project>_lorapedia-data`; `docker volume ls` shows it.)

## Things to know

- A radio that re-appears when its serial port is closed (the RAK4631 does) can leave the container holding a stale device
  mapping after an unplug or reset; `docker compose restart lorapedia` fixes it. A WiFi/network radio avoids the issue.
- Bluetooth radios do not work inside Docker.
- Only one Ollama should use the GPU at a time. If you also run Ollama on the host, stop its models (or the service) first.
