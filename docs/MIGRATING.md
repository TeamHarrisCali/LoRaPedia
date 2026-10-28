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

## 2. Add the GPU (a `docker-compose.override.yml`)

Machine-specific settings go in `docker-compose.override.yml`, not in `docker-compose.yml`. Compose loads it by itself, **the updater
includes it when it replaces the app container (so your radio mapping survives automatic updates)**, and git ignores it.

```bash
cp docker-compose.override.example.yml docker-compose.override.yml
```

Then edit it. For the **GPU**, keep the `ollama` `deploy:` block (it needs the NVIDIA Container Toolkit above).

**The USB radio needs nothing here.** The compose file finds any Meshtastic radio plugged into USB by itself, whatever its brand or
group (`dialout`, `uucp`, `plugdev`...), so there is no device path or group number to look up. Plug it in before or after starting.

The shipped compose file already sets `OLLAMA_CONTEXT_LENGTH=4096` (Ollama's default context made `llama3.2:3b` occupy about 4 GB of
GPU memory; 4096 needs about 2.4 GB, so both models fit a 4 GB card) and keeps the GPU kernel cache in the models volume.

**First start on an NVIDIA GPU is slow, once:** the first answer takes about two minutes while the GPU compiles its kernels, then
about five seconds, including after the container is recreated (measured on an RTX 3050 Ti: 127 s, then 5 s).

No radio yet? The node starts in simulator mode and picks the radio up when you plug it in.

## 3. Start it

```bash
docker compose up -d
docker compose exec ollama ollama pull llama3.2:3b          # the models are not copied: pull them once (about 2.3 GB in total)
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

- A radio that re-appears when its serial port is closed or after a reset (the RAK4631 does) is found again by itself, because the
  container scans the host's device files rather than holding one fixed path. If a radio was plugged in AFTER the container started
  and its device file has a group the container has not seen, run `docker compose restart lorapedia` once.
- Bluetooth radios do not work inside Docker.
- Only one Ollama should use the GPU at a time. If you also run Ollama on the host, stop its models (or the service) first.
