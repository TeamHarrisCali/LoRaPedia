# Project: LoRaPedia

Build **LoRaPedia**, a self-hosted, fully offline Q&A node. People on a Meshtastic
LoRa mesh send it a question as a text message. It searches a local offline copy of
Wikipedia using RAG, generates an answer with a local LLM, and replies over the mesh
inside Meshtastic's tiny message size limit. It has a clean web UI for setup and
day-to-day management. It is inspired by Project NOMAD, but focused on mesh-delivered
answers.

## Hard requirements

- **100% offline at runtime.** No cloud APIs, no telemetry, no CDN assets (bundle
  fonts/icons locally). Internet is only needed once, to download models and ZIM
  files, and the UI should make that a clearly labeled optional step.
- **Runs on modest hardware**: a mini PC, laptop, or Raspberry Pi 5 / Jetson, and
  works without a GPU (slower is fine). Show hardware-aware model suggestions.
- **Meshtastic message limit**: a single text payload is about 233 bytes max.
  Use a configurable limit (default **200 bytes**, a safe margin) and count
  **UTF-8 bytes, not characters**. Answers must fit this limit.
- **Setup in under 10 minutes** via the UI wizard, with a single
  `docker compose up` (or `./run.sh`) start.

## Suggested stack (change it if you have a better reason, and explain why)

- **Backend:** Python 3.11+, FastAPI, SQLite (via SQLModel or SQLAlchemy), WebSocket
  for live updates, background worker tasks.
- **Radio:** the official `meshtastic` Python library (serial, TCP, and BLE
  interfaces) with `pypubsub` for receive events.
- **Wikipedia:** Kiwix **ZIM** files read with `python-libzim`. Support
  `wikipedia_en_top`/`mini`/`nopic` variants as well as the full dump.
- **RAG:** chunk the article lead plus key sections, embed with a local embedding
  model (e.g. `nomic-embed-text` via Ollama), and store in `sqlite-vec`
  (or FAISS/Chroma). Use hybrid retrieval: BM25/FTS5 on titles and text + vector
  similarity, then rerank the top results.
- **LLM:** Ollama as the default local runtime (OpenAI-compatible endpoint abstraction
  so llama.cpp or LM Studio can be swapped in). Default to a small model
  (e.g. 1B to 3B class) with a model picker.
- **Frontend:** React + Vite + TypeScript + Tailwind (or SvelteKit), served by the
  backend. Bundle all assets locally.

## Core behavior

### 1. Message handling
- Listen for incoming text messages. Support two trigger modes (configurable):
  1. **Direct messages** to the node (default, least noisy)
  2. **Channel messages** that start with a trigger prefix (default `?` or `!wiki`)
- Ignore the node's own messages, non-text packets, and messages from blocked nodes.
- **Per-node rate limiting** (default: 1 query per 60s, configurable) plus a global
  queue so answers go out one at a time, with a configurable delay between sends to
  respect LoRa airtime. Reply with a short "busy, try again in Ns" if limited.
- Allowlist / blocklist of node IDs, editable in the UI.
- Reply to the original sender (DM reply, or channel reply prefixed with
  `@ShortName`). Respect the configured hop limit and channel index.
- Built-in commands: `?help`, `?more` (next part of the last answer), `?stats`.

### 2. Answer generation (the key constraint)
- Retrieve top-k chunks, then prompt the LLM with a strict system prompt:
  answer **only from the provided context**, be factual, no preamble, no markdown,
  no emoji, plain ASCII where possible, and say "not found" when the context
  doesn't support an answer.
- **Fit to the byte budget** with a guaranteed-safe pipeline:
  1. Ask the LLM for a short answer targeting the budget.
  2. Measure UTF-8 bytes. If over, ask the LLM to compress once.
  3. If still over, truncate at a word boundary and append `...`.
- **Optional multi-part mode** (off by default): split longer answers into numbered
  parts like `(1/3)`, each within the limit, sent sequentially, with `?more`
  for on-demand continuation. Cap the max parts (default 3).
- Optionally include a short source tag, e.g. ` [wp:Photosynthesis]`, if
  there is room (toggle in settings).
- Write unit tests for the byte-limit logic, including multi-byte characters.

### 3. Knowledge base management
- Library page to **download, import, and remove ZIM files** (point at a local file,
  or download from Kiwix with progress, speed, and resume).
- **Indexing pipeline** with a visible progress bar, ETA, pause/resume, and the
  ability to index a subset first (e.g. "lead sections only", "top 100k articles")
  so users get a working system fast and can expand later.
- Show index stats: article count, chunk count, disk usage, last indexed.
- Handle the full dump gracefully (streaming, batched embeddings, resumable).

### 4. Simulation / mock mode
- A **radio simulator** (no hardware needed) that lets me type as a fake node in the
  UI to test the entire flow. This is also the default when no device is detected.

## UI requirements ("intuitive to manage")

A non-technical person should be able to set this up and run it. Use plain language,
inline help text, sensible defaults, and visible status everywhere.

### Pages
1. **Setup Wizard** (first run, resumable): Detect hardware, then Pick LLM, then
   Connect radio, then Add Wikipedia, then Index, then Test. Each step shows a
   green check when done and can be skipped or revisited.
2. **Dashboard**: status cards (Radio, LLM, Knowledge Base, Queue), queries today,
   average response time, a 24h activity sparkline, and a "Send test question" box.
3. **Live Feed**: real-time conversation log showing sender, question, retrieved
   sources, the final answer, byte count (e.g. `187/200 B`), and latency. Click a
   row to expand the retrieved chunks and the full prompt for debugging.
4. **Knowledge Base**: ZIM library, indexing controls, and a **search playground**
   where I type a query and see ranked chunks with scores.
5. **Model**: choose LLM and embedding model, pull models with progress, tune
   temperature and max tokens, edit the system prompt (with a "reset to default"
   button), and a live "try it" box that shows the byte count.
6. **Radio**: connection method (USB/serial auto-detect, TCP, BLE), channel selector,
   trigger mode and prefix, hop limit, byte limit slider (50 to 233), multi-part
   toggle, delay between sends, and a **node list** with block/allow toggles.
7. **Settings**: rate limits, source tags, data directory, backup and restore
   config, light/dark theme, and an Export logs (CSV) button.

### UX details
- Persistent top status bar: radio dot, LLM dot, index dot, queue length.
- Toasts for success and error states, with actionable error messages
  (e.g. "No radio found. Is it plugged in? [Retry] [Use simulator]").
- A byte-budget meter component, reused anywhere text is previewed.
- Keyboard-friendly, responsive down to tablet width, accessible contrast (WCAG AA).

## Visual design direction

**Concept: "field radio meets library card catalog."** Warm, rugged, trustworthy,
not a generic dashboard.

- **Palette (dark default):**
  - Background `#0F1412` (deep charcoal-green), surface `#171E1B`, border `#26312C`
  - Primary accent: signal amber `#FFB020` (active states, key buttons)
  - Secondary: mesh teal `#2DD4BF` (connected, success)
  - Danger `#F2545B`, text `#E8EDEA`, muted text `#8FA096`
  - Light theme: paper `#F6F3EA`, ink `#1B2420`, same accents
- **Type:** headings in a slab or humanist serif (e.g. Fraunces or IBM Plex Serif),
  UI text in Inter or IBM Plex Sans, and **monospace (JetBrains Mono)** for
  messages, node IDs, and byte counts. Bundle fonts locally.
- **Motifs:** subtle topographic contour lines in the background, a signal-strength
  glyph next to node names, and an antenna/book logo mark. Messages render as chat
  bubbles in monospace, with a thin amber bar showing the byte budget fill.
- **Motion:** gentle pulse on the radio status dot when a packet is received,
  and a typewriter-style reveal for new answers in the Live Feed. Respect
  `prefers-reduced-motion`.

### Layout wireframe (Dashboard)

```
+------------------------------------------------------------------+
|  [antenna-book] LoRaPedia     o Radio  o LLM  o Index  Queue: 0   |
+-------------+----------------------------------------------------+
|  Dashboard  |  Good evening. Your node is online.                |
|  Live Feed  |                                                    |
|  Knowledge  |  +---------+ +---------+ +---------+ +---------+   |
|  Model      |  | Radio   | | LLM     | | Wikipedia| | Today  |   |
|  Radio      |  | Heltec  | | llama3.2| | 6.2M     | | 42 Qs  |   |
|  Settings   |  | -92 dBm | | 3B ready| | articles | | 3.1s   |   |
|             |  +---------+ +---------+ +---------+ +---------+   |
|             |                                                    |
|             |  Activity (24h)  ____/\___/\_______/\__            |
|             |                                                    |
|             |  Recent                                            |
|             |  !a1b2 "what is a quasar?"                         |
|             |   > Quasars are extremely luminous active ...      |
|             |   [##########--] 187/200 B   2.9s                  |
|             |                                                    |
|             |  Test a question: [____________________] [Ask]     |
+-------------+----------------------------------------------------+
```

## Deliverables

1. Full source code with a clear folder layout (`backend/`, `frontend/`,
   `docker/`, `docs/`), and a `docker-compose.yml` (with an Ollama service and a
   USB serial device passthrough example).
2. A README covering: what it is, hardware requirements, quick start, how to download
   ZIM files, how to flash and configure the Meshtastic device, and troubleshooting.
3. Tests for the byte-limit/splitting logic, the trigger parsing, and rate limiting.
4. Seed/demo data so the UI looks alive in simulator mode.
5. A short `ARCHITECTURE.md` with a diagram of the message flow:
   `Mesh -> Radio listener -> Queue -> Retriever -> LLM -> Byte fitter -> Sender -> Mesh`.

## How to work

- **Plan first.** Briefly outline the architecture and your phases, then build in
  phases, running and testing as you go:
  1. Backend skeleton + simulator + byte-fitting logic (with tests)
  2. RAG pipeline on a small sample ZIM
  3. Real Meshtastic integration
  4. UI pages and the setup wizard
  5. Polish, docs, Docker
- Ask me before making any major deviation from this spec. For small ambiguities,
  choose a sensible default and note it in the README.
- Keep dependencies lean, code typed and commented where non-obvious, and make
  every setting configurable from the UI (not just env vars).

Start with the plan and the project scaffold.
