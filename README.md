# LoRaPedia

**An offline Wikipedia you can text over a Meshtastic mesh.**

Someone on your LoRa mesh sends a question as a normal text message. LoRaPedia searches a local copy of
Wikipedia, has a small local language model write a short answer from what it found, and replies over the
mesh, trimmed to fit Meshtastic's tiny message size. No internet, no cloud, no telemetry while it runs.

It is inspired by Project NOMAD, but focused on one job: answers delivered over the mesh.

```
  Person on the mesh:   what is a quasar?
  LoRaPedia:            A quasar is an extremely luminous active galactic nucleus powered by a
                        supermassive black hole that is accreting matter. [wp:Quasar]      132/200 B
```

A web interface runs the whole thing: a setup wizard, a live feed of every question and answer, and pages for
the Wikipedia library, the model, and the radio. A non-technical person can set it up in about ten minutes.

- [What you need](#what-you-need) · [Quick start](#quick-start) · [Getting Wikipedia files](#getting-wikipedia-files)
- [Setting up the Meshtastic radio](#setting-up-the-meshtastic-radio) · [Using it](#using-it) · [Settings](#settings)
- [Security](#security) · [Troubleshooting](#troubleshooting) · [Development](#development) · [Design decisions](#design-decisions)

---

## What you need

| | Minimum | Comfortable |
|---|---|---|
| **Computer** | Any 64-bit x86 or ARM64 machine: mini PC, laptop, Raspberry Pi 5, Jetson | Same, with more memory |
| **Memory** | 4 GB (1B model, small Wikipedia file) | 8 to 16 GB (3B model) |
| **Disk** | 3 GB free (Quick Reference plus a model) | 50 GB+ for Complete (Compact); see the package table |
| **GPU** | Not needed | Makes answers faster; an NVIDIA card with 6 GB+ allows the 8B model |
| **Radio** | None (use the built-in simulator) | Any Meshtastic device: Heltec, T-Beam, RAK, T-Echo... |

No GPU is fine. Answers just take longer (seconds to a minute on a CPU), which suits a mesh where messages
already take seconds to travel. The setup wizard detects your hardware and recommends a model that fits.

Software: **Docker** (easiest), or **Python 3.11+** and **Node.js 20+** for `./run.sh`. You also need
[Ollama](https://ollama.com) to run the language model (included in the Docker setup).

## Quick start

Internet is needed **once**, to fetch software, a model, and a Wikipedia file. After that it runs offline.

### Option A: Docker (recommended)

```bash
docker compose up -d
```

Open **http://localhost:8000**. The first visit starts the setup wizard.

This starts LoRaPedia and an Ollama server. Data lives in Docker volumes and survives restarts. For a USB radio
see [the USB section of docker-compose.yml](docker-compose.yml) (two lines to uncomment).

### Option B: without Docker

```bash
./run.sh
```

It creates a Python environment, builds the web UI, and starts the server on http://localhost:8000. Install and
start [Ollama](https://ollama.com) yourself first. To let other devices on your network open the UI:
`HOST=0.0.0.0 ./run.sh` (read [Security](#security) first). Bluetooth radios work with this option only.

### The setup wizard

Six steps, each skippable and revisitable. Progress is saved, so closing the tab is fine.

1. **Check this computer.** Shows what hardware was found.
2. **Pick a language model.** Recommends one for your hardware and installs it with a progress bar. Short on
   time? "Use demo mode" lets you try everything with nothing to download.
3. **Connect your radio.** Plug it in and press Retry, or use the simulator.
4. **Add Wikipedia.** Use the 12-article sample, a file you have, or download one (see below).
5. **Build the index.** Make it searchable. Start with a small subset and expand later.
6. **Test it.** Asks a question end to end.

No radio yet? Everything works with the **simulator**: type as a fake node from the Dashboard. Nothing is
sent over the air.

## Getting Wikipedia files

On the **Knowledge** page (and in the wizard) pick a Wikipedia **package** and press **Install**. LoRaPedia downloads
it from [Kiwix](https://www.kiwix.org), adds it to the library, and builds the search index, all in one step.
The newest version is looked up when you press the button. This is the only part of LoRaPedia that goes online,
and only when you press it. Downloads show progress and speed and **resume** if interrupted.

| Package | Download | Free space to plan for\* | What it is |
|---|---|---|---|
| **Quick Reference** (recommended) | ~300 MB | ~1.2 GB | The most-read articles, introductions only |
| Popular Articles | ~2.2 GB | ~9 GB | The same articles with their full text |
| Complete (Compact) | ~12.5 GB | ~50 GB | Every English article, introductions only |
| Complete (Full Text) | ~53 GB | ~210 GB | Every article, full text. Very large |

\* The search index is stored beside the download and is **about three times larger than it**, because each chunk of
text stores a 768-number vector. That ratio was measured on Quick Reference only; the larger rows apply the same
factor and full-text editions have more chunks per article, so treat them as optimistic. The installer refuses to
start if the disk is too small, and refuses *before* downloading if the embedding model is missing.

Image editions are not offered: LoRaPedia never uses images.

**How long indexing takes.** It is dominated by the embedding model, which on one test laptop (RTX 3050 Ti,
`nomic-embed-text`) handled roughly 60 to 130 chunks per second, i.e. about 6 to 26 articles per second depending on
what else was using the GPU. Quick Reference (about 50,000 real articles, about 200,000 chunks) therefore takes
roughly an hour or two. It is safe to stop and resume, and you can ask questions meanwhile; the index is built in
the file's order (alphabetical), so until it finishes, only the part already done can answer.

**Counting articles.** Kiwix files list redirects as articles, and over 90% of a Wikipedia file's entries are
redirects. LoRaPedia estimates the real article count by sampling, so figures are shown as "about".

Other ways to add a file (Knowledge page, *Other files and advanced options*):

- **Import a file you already have.** Enter its path.
- **Copy it** into the `zim/` folder inside the data directory and restart.
- **Paste a Kiwix link**, or browse every English Wikipedia variant Kiwix lists.
- **The 12-article sample**, to try everything before downloading anything.

**Indexing a subset.** For your own files you can index "first 10,000" or "intro only". "First N" means the first N
in the file's own order, which is alphabetical, **not** the most popular, so a partial index is lopsided. Prefer
letting it finish, or use a smaller package.

If you change the embedding model after indexing, the index must be rebuilt (different models make incompatible
vectors). The installer asks before doing this, and your files are never deleted by it.

## Setting up the Meshtastic radio

Short version. Details and tips are in [docs/MESHTASTIC.md](docs/MESHTASTIC.md).

1. **Flash current firmware** with the [Meshtastic web flasher](https://flasher.meshtastic.org) (Chrome or Edge).
2. **Set your region** (required before it will transmit), a device name, and any channel settings.
3. **Plug it into the computer running LoRaPedia** with a USB cable that carries data (some are charge-only).
   On Linux, add your user to the serial group (`dialout` on Debian/Ubuntu, `uucp` on Arch) and log in again.
4. In LoRaPedia go to **Radio**, choose *Automatic (USB)*, and press **Connect**.

Prefer WiFi? Enable WiFi in the radio's network settings, then choose *Network* and enter its address. Bluetooth
is supported too, outside Docker.

Dedicate a radio to LoRaPedia. Give it a memorable name (e.g. `WIKI`) so people know who to message.

## Using it

Anyone on the mesh can **send a direct message** to the LoRaPedia node with their question. That is the default
and the quietest option: only the asker sees the reply.

Optionally allow **channel questions**: choose *In a channel, starting with a prefix* on the Radio page, and
people write `?what is a quasar` or `!wiki quasar`. The reply goes to the channel, starting with `@Name`.

| Message | What happens |
|---|---|
| any question | Searched, answered, and sent back |
| `?help` | Short usage hint |
| `?more` | Next part of a long answer (when parts are sent on request) |
| `?stats` | Questions today, average time, queue length |

**Message size.** A Meshtastic text message holds about 233 bytes. LoRaPedia counts **UTF-8 bytes, not
characters** (accented letters and symbols cost 2 to 4), and uses a configurable limit, **200 by default**. An
answer is made to fit like this:

1. The model is asked for a short answer within the limit.
2. If it is too long, it is asked **once** to compress it.
3. If still too long, it is cut at a word boundary and ends with `...`. This last step guarantees the limit.

**Long answers.** If an answer genuinely needs more room, LoRaPedia can send it as numbered parts like `(1/3)`,
each within the limit, up to a **maximum number of parts you set** (default 3). Parts go out one after another
with a pause between them, or the first part goes out and the rest wait for `?more`. Turn this off to always
send one message.

**Fair use of the airwaves.** Each node is limited to **1 question per 60 seconds** by default (a "busy, try again
in Ns" reply is sent otherwise), answers leave one at a time, and there is a delay between messages. All
adjustable. You can block nodes, or allow only listed ones, on the Radio page.

## Settings

Everything is editable in the UI; nothing requires environment variables. Where to find it:

| Page | You can change |
|---|---|
| **Radio** | Connection (USB / network / Bluetooth / simulator), channel, trigger mode and prefixes, hop limit, **byte limit (50 to 233)**, multi-part and its cap, pause between sends, node block/allow lists |
| **Model** | Language and embedding model (install with progress), temperature, max tokens, passages retrieved, system prompt (with *Reset to default*), a "try it" box showing the byte count |
| **Knowledge** | Wikipedia files, downloads, indexing, a search playground |
| **Settings** | Rate limits, queue length, source tags (`[wp:Title]`), theme, backup and restore, CSV export of the log, demo data |

Environment variables exist for deployment only: `LORAPEDIA_DATA` (data folder), `LORAPEDIA_HOST`,
`LORAPEDIA_PORT`, and `LORAPEDIA_OLLAMA_URL` (where Ollama is, used for first-run defaults).

**Backups.** *Settings → Back up settings* downloads your configuration. To back up everything, copy the data
directory (databases and Wikipedia files).

## Security

**The web interface has no password.** Anyone who can open it can change settings, delete the library, and start
downloads. So:

- By default it listens on **this computer only** (`127.0.0.1`). The Docker setup is published the same way.
- Only open it to your local network (`HOST=0.0.0.0` or changing the port mapping) on a network you trust.
- **Never expose it to the internet.** If you need remote access, put it behind a VPN or an authenticating proxy.

Text arriving from the mesh is untrusted input. LoRaPedia only ever turns it into a search and a text reply. The
model has no tools and cannot run anything, so the worst outcome of a hostile question is a bad answer.

## Troubleshooting

| Symptom | What to do |
|---|---|
| **"No radio found. Is it plugged in?"** | Check the USB cable carries data (try another). Press Retry. Run `ls /dev/ttyACM* /dev/ttyUSB*`: if nothing appears, the computer does not see the radio. Meanwhile the simulator keeps everything usable. |
| **"Permission denied" on the serial port** | Add your user to the serial group (`sudo usermod -aG dialout $USER`, or `uucp` on Arch) and log out and in. In Docker, set `group_add` as described in docker-compose.yml. |
| **"The serial port is busy"** | Another program has it: the Meshtastic app or CLI, or ModemManager on Linux. Close them, or stop ModemManager (`sudo systemctl stop ModemManager`). |
| **"Several radios found"** | Choose *USB, choose the port* and pick one. |
| **Radio connects but nobody gets answers** | Check the trigger mode: the default needs a **direct message** to this node. In channel mode the message must start with the prefix and be on the configured channel. Look at the Live Feed to see whether the question arrived. |
| **"LLM not ready" / "Cannot reach Ollama"** | Start Ollama (`ollama serve`, or the `ollama` container). In Docker the address is `http://ollama:11434`. See the Model page. |
| **"Model … is not installed"** | Install it from the Model page, or `ollama pull <name>`. |
| **Indexing fails: embedding model not installed** | Install `nomic-embed-text` on the Model page, or turn on *Keyword-only search* there to index without one. |
| **"Re-index needed"** | The index was built with a different embedding model. Remove the library entry and index again. Keyword search keeps working meanwhile. |
| **Every answer is "Not found in my offline library"** | Nothing relevant was retrieved. Open the question in the Live Feed to see what was found, and use the search playground. Is the right file indexed? Is only the demo data loaded? |
| **First answer is very slow** | The model is loading into memory. Later answers are faster. |
| **Answers end in `...`** | They were cut to fit. Allow more parts, raise the byte limit (up to 233), or lower *max tokens*. |
| **"Busy, try again in Ns"** | The per-node rate limit. Adjust it under Settings. |
| **Page is blank or looks broken after an update** | Hard-refresh the browser (Ctrl+Shift+R). |
| **Download fails or stalls** | It keeps its progress: press Resume. This step needs internet; everything else does not. |
| **Bluetooth radio not found in Docker** | Not supported in Docker. Use `./run.sh`. |

Still stuck? The server prints a log (`docker compose logs lorapedia`, or the terminal running `./run.sh`).

## Development

```bash
# backend (Python 3.11+)
cd backend
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest                         # 150+ tests; no hardware or models needed
.venv/bin/python -m lorapedia            # API on :8000

# frontend (Node 20+), in another terminal
cd frontend
npm install
npm run dev                              # http://localhost:5173, proxies /api and /ws to :8000
npm run build                            # outputs frontend/dist, which the backend serves
```

Layout:

```
backend/    FastAPI app (lorapedia/), tests (tests/)
frontend/   React + Vite + TypeScript + Tailwind UI
docker/     Dockerfile (multi-stage: build UI, then slim Python runtime)
docs/       Meshtastic setup guide
```

The tests cover the byte-limit and splitting logic (including multi-byte text), trigger parsing, rate limiting,
the message engine, the ZIM reader and indexer, retrieval, the radio adapter (against a fake device), the
downloader, and the API. The web UI was tested by hand in a browser; it has no automated tests yet.

Architecture and the message flow are in [ARCHITECTURE.md](ARCHITECTURE.md).

## Design decisions

Where the build departs from the original brief or chose a default for something unspecified:

- **Plain `sqlite3`, not SQLModel/SQLAlchemy.** The schema is small and fixed, so an ORM adds dependencies
  without helping.
- **Multi-part answers are on by default**, capped at 3 parts, as requested ("send in chunks if need be, up to an
  admin-specified amount"). The brief had it off by default. Turn it off on the Radio page.
- **The retrieval rerank is lexical**, not a neural cross-encoder: hybrid BM25 + vector search is fused, then
  re-scored by title match and query coverage. It runs on any CPU. A cross-encoder could slot in later.
- **A keyword-only fallback exists** (hashed vectors) so indexing works before any embedding model is installed.
  It matches words, not meaning.
- **"First N articles" is file order**, not popularity. A ZIM has no popularity data; use a `top_*` file.
- **The data directory is not changeable from the UI.** It cannot be moved while running; set `LORAPEDIA_DATA`
  or the Docker volume instead. The Settings page shows where it is.
- **The UI uses hash routes** (`/#/radio`), so the server needs no special URL handling.
- **The UI simulator uses a fresh fake node per test question**, so the per-node rate limit does not get in the
  way of quick tests.

## Limitations

- Verified on one real radio and real Wikipedia (the Quick Reference package, indexed with `nomic-embed-text`): a
  **RAK4631 over USB** exchanged a direct message with a phone-connected node and the answer came back (about 1.5 s
  with a local 3B model). Network and Bluetooth connections, other devices, and channel-mode replies are only tested
  against a fake radio.
- Only the Quick Reference package has been installed. The larger packages' download, indexing time and disk use are
  extrapolated, not measured.
- Retrieval quality was spot-checked on a few dozen questions, not benchmarked.
- English Wikipedia only has been considered; other languages should work if the ZIM is HTML but are untried.
- One radio at a time.

## License

[MIT](LICENSE). Third-party software and Wikipedia content have their own licenses: Wikipedia text is
CC BY-SA, and ZIM files carry the license of their source.
