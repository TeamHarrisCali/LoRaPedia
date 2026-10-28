# Architecture

LoRaPedia is one Python process (FastAPI) plus an LLM server (Ollama) and a built web UI. Everything runs
locally.

## Message flow

```
                            one worker, answers go out one at a time
                                          |
  Mesh --> Radio listener --> Queue --> Retriever --> LLM --> Byte fitter --> Sender --> Mesh
            (filter, parse,   (FIFO)    (hybrid       (local,    (guaranteed     (reply to
             rate-limit)                 search)       Ollama)    <= limit)       sender)
```

In words:

1. **Radio listener.** A text message arrives from the radio (or from the UI simulator). It is dropped if it is
   from our own node, a blocked node, a node outside a non-empty allow list, or is not a **direct message**
   (public channel messages are never answered). The trigger rules decide whether it is a *question*, a *command* (`?help`, `?more`, `?stats`, `?ping`, `?reset`) or noise.
2. **Rate limit and queue.** Each node gets a number of questions per time window. A question inside the cooldown is
   not dropped: the limiter *reserves the node's next free slot* (a burst lines up one window apart), the question is
   held until then, and an acknowledgment says when to expect the answer. A per-node cap and a global cap bound the
   backlog; past them a "busy" reply is sent. Everything ready goes onto a single queue, so replies leave one at a
   time. (A setting restores the plain "busy, try again in Ns" behavior.)
3. **Retriever.** The question is searched in the local index (keyword + vector, fused and reranked).
4. **LLM.** The top passages and the question are sent to the model with a strict system prompt: answer only from
   the context, no markdown, "not found" if unsupported.
5. **Byte fitter.** The answer is cleaned and forced to fit the byte limit (see below).
6. **Sender.** The reply goes to the original sender as a direct message, with the configured hop limit and a pause between parts.
   Nothing in this path can post to a public channel: the parser ignores public messages, the engine refuses to send for one,
   the radio manager refuses a message with no addressee, and the Meshtastic adapter's `send_text` has no broadcast at all. (A post you type
   yourself on the Messages page uses a separate method, `post_public`, that the engine never imports or calls: see `messaging.py`.)
   Each part sent over a real radio is followed: the adapter matches the radio's routing acknowledgements to the packet ids it sent and
   `deliveries` records sent / relayed / delivered / failed (an acknowledgement from the addressee is delivered, one from a neighbour only relayed).

If nothing relevant is retrieved (or the model says the context does not answer), the reply depends on how far
indexing has got. Once every article is searchable (`library_state` is `covered` or `complete`), "no match" is certain,
so the model is asked once more, with a different prompt and no context, to answer from its own knowledge. The code
prepends `[AI memory - may be wrong] ` itself, and a model that says "I don't know" gets a plain "not found". While
indexing is still on pass 1 the reply is "Not found yet" and the model is not consulted, because the article may simply
not be indexed yet.

## Components

```
                        +-------------------- browser (React UI) --------------------+
                        |  REST (/api/*)                          WebSocket (/ws)    |
                        +--------------------+---------------------------+-----------+
                                             |                           ^
                         +-------------------v---------------------------+-----------+
                         |                     FastAPI app (api.py)                   |
                         |  settings, library, indexing, models, radio, wizard, logs  |
                         +--+--------------+------------------+-----------------+-----+
                            |              |                  |                 |
                      +-----v----+   +-----v------+    +------v------+    +-----v------+
  mesh <--LoRa--> radio | RadioMgr |   |   Engine   |    |  Indexer    |    |  Store     |
                      | (serial/ |-->| queue,     |    |  (resumable)|    |  settings, |
                      |  tcp/ble |   | limits,    |    +------+------+    |  query log |
                      |  or sim) |<--| fit, send  |           |           +------------+
                      +----------+   +--+------+--+    +------v------+
                                        |      |       |  Index (SQLite: chunks + FTS5 + vec) |
                               +--------v+  +--v----------+   ^
                               |Retriever|  | LLM client  |   | reads
                               | (hybrid)|--> (OpenAI-    |   |
                               +----+----+  |  compatible)|  ZIM files (python-libzim)
                                    |       +------+------+
                                    v              v
                                 Index         Ollama (local)
```

| Module (`backend/lorapedia/`) | Responsibility |
|---|---|
| `engine.py` | Filters, queue, worker, prompt building, `generate()`, sending. The heart of the flow. |
| `fitter.py` | Byte counting, text cleaning, truncation, splitting into parts. Pure functions. |
| `triggers.py` | Question / command / ignore decision for a message. Pure. |
| `meshask.py` | Questions about the mesh itself: a rule-based router and a fixed menu of read-only lookups over `mesh.db`. No model, no transmitting; replies use only a node's own name, never your label. |
| `messaging.py` | What the operator types: a direct message to one node, a chat window (what the node says back is kept for the operator, not answered), and an opt-in public post. The only caller of `post_public`; the engine gets only a callback to step aside. |
| `memory.py` | Each sender's last few answered questions for follow-ups. RAM only, per sender, expires after idle time. |
| `ratelimit.py` | Per-node sliding window. Takes an injectable clock. |
| `radio/manager.py` | Chooses the real radio or the simulator, reconnects with backoff, forwards received packets. |
| `radio/meshtastic_radio.py` | The `meshtastic` library over serial/TCP/BLE; bridges its threads to asyncio. |
| `radio/simulator.py` | A radio that records what would be sent. |
| `zim.py` | Reads ZIM files, extracts clean text from the HTML, chunks the lead and key sections. |
| `index.py` | The SQLite index and the resumable `Indexer`. |
| `embed.py` | Embedding backends: Ollama, or a hashing fallback with no dependencies. |
| `retrieval/hybrid.py` | BM25 + vector search, rank fusion, rerank. |
| `llm/openai_compat.py` | Chat client for Ollama, llama.cpp, LM Studio (OpenAI-style API). |
| `download.py`, `ollama.py` | Resumable ZIM downloads and Kiwix catalog; model listing and pulls. |
| `store.py` | Settings and the query log (SQLite). |
| `mesh/observe.py` | Turns a received packet or the radio's node list into checked facts (positions, sensor readings, names, hops, signal). Pure. |
| `mesh/store.py` | `mesh.db`: remembered nodes, telemetry, position trails, hourly packet/signal/hop counts, your notes; pruning. |
| `mesh/service.py` | Buffers what the radio hears on the event loop, writes it in batches, and builds the node list and summary. Never transmits. |
| `mesh/sim.py` | A made-up mesh for demos and tests. |
| `backups.py` | Verified backups of that database: daily, before updates and restores; download, upload, restore. |
| `sources.py` | Which retrieved article an answer came from, for the web panel only (nothing about it is sent over the radio). |
| `system.py`, `demo.py`, `sample.py` | Hardware detection; demo data; a tiny generated ZIM. |
| `api.py` | HTTP and WebSocket routes, wiring, static UI serving. |

The radio, LLM, embedder and retriever are each hidden behind a small interface, which is what lets the
simulator, the mock model, and a fake radio stand in for real ones in tests and demos.

## The byte-fitting pipeline

The 233-byte Meshtastic limit is a hard physical constraint, so fitting is built to **never** exceed it,
regardless of what the model does.

```
  budget = limit - len(" ?more")                    (only when parts wait for ?more)
  total  = parts_allowed * (budget - len("(3/3) "))     (1 part: just budget)

  1. Prompt the LLM: "at most <budget> bytes; only if essential, up to <total>"
  2. clean:  strip markdown, fold smart quotes to ASCII, collapse whitespace
  3. over <total> bytes?  ask the LLM ONCE to rewrite within <total>
  4. split:  fits in one message -> send as is
             else wrap at word boundaries into "(i/n) " parts, at most <parts_allowed>;
             overflow is truncated at a word boundary and ends with "..."
```

Lengths are always UTF-8 **bytes**. Truncation never splits a multi-byte character, and a single word longer
than a part is hard-split. Step 4 is the guarantee; steps 1 and 3 only improve quality. The radio adapter also
refuses to send anything over 233 bytes, as a last line of defence.

## Knowledge base

**Indexing.** The default is two passes over the file. Pass 1 indexes only the first chunk of every article; pass 2 (from
the same starting point) adds the remaining chunks. `index_state.phase` (`first`, `rest`, `all`) and `rest_from` record
where an interrupted run stands, so a restart resumes the same pass. `ZimReader` streams HTML entries (skipping redirects and non-HTML). `chunk_article` strips noise
(infoboxes, citations, navigation, "See also", "References") and produces the lead plus up to five more sections,
each split at sentence boundaries to about 800 characters. A batch of articles is embedded, then written in one
transaction **together with a checkpoint** (the last entry id). A crash or a stopped embedder therefore loses at
most the batch in flight, and *resume* continues at the checkpoint.

**Storage** (`index.db`, SQLite):

| Table | Purpose |
|---|---|
| `zims` | The library: files and their metadata |
| `chunks` | Text of each chunk, with title and section |
| `chunks_fts` | FTS5 over title and text (Porter stemming, diacritics folded); title weighted 8x in BM25 |
| `vec_chunks` | `sqlite-vec` table of normalised embeddings, keyed by chunk id |
| `index_state` | Per-file checkpoint, counts, status |
| `meta` | Which embedder built the vectors (mixing embedders is refused) |

**Retrieval.** Keyword (FTS5/BM25) and vector (nearest neighbours) each return 60 candidates. They are fused with
reciprocal rank fusion, then reranked by title match, query-term coverage and a small lead-chunk bonus. For a question shaped
"attribute of subject" ("capital of Japan", "largest city in Canada") the article named by the subject gets a further boost:
look-alike pages that repeat the question's words (every Japanese city is "the capital of" something in Japan) otherwise fill the
four slots and the page that holds the answer never reaches the model. A
vector-only hit must clear a similarity floor, so an unrelated question retrieves nothing and gets "not found"
instead of a confident wrong answer. At most two chunks per article reach the prompt. If the embedder is down,
search quietly degrades to keyword-only.

## Concurrency

- One asyncio event loop runs the API, the engine's single worker, the radio supervisor, and the indexer.
- The `meshtastic` library is synchronous and publishes received packets on **its own thread** via pypubsub. The
  adapter hands each packet to the event loop with `run_coroutine_threadsafe`. Blocking calls (connect, send,
  close) run in `asyncio.to_thread`.
- ZIM reading and HTML parsing run in a thread per batch so the loop stays responsive while indexing.
- The radio supervisor reconnects with backoff, and in *auto* mode notices a device plugged in later.

## Data on disk (`LORAPEDIA_DATA`, `/data` in Docker)

```
data/
  lorapedia.db     settings, query log, wizard state
  index.db         chunks, FTS5, vectors, library, checkpoints
  mesh.db          nodes the radio has heard, their telemetry, positions and signal (pruned by age); your node labels and notes
  zim/             Wikipedia files (and *.part while downloading)
```

## Frontend

React + TypeScript + Tailwind, built by Vite into static files that the backend serves, so there is one thing
to run. Fonts and icons are bundled; no request leaves the machine. A WebSocket pushes new questions and radio
status; everything else is polled REST. Settings pages edit a local draft and save only the fields you touched, so
immediate actions elsewhere (like blocking a node) are never overwritten by a stale form.

## Updates

```
   GitHub Releases API <--(daily GET, strict parse)-- app (UpdateService)         Kiwix listing <--(daily)-- app (WikipediaUpdater)
                                                         |  POST /v1/apply (no parameters, bearer token)
                                                         v
                                              updater container (holds the Docker socket)
                          GitHub again (its own lookup) -> docker pull ghcr.io/.../lorapedia:<v> -> cosign verify (exact identity)
                          -> .env LORAPEDIA_IMAGE=<digest ref> -> docker compose up -d --no-deps lorapedia
                          -> wait healthy + reports <v>  --fail-->  restore lorapedia-rollback:previous
```

On the beta channel (off by default) the app reads the last few releases and the updater also accepts `vX.Y.Z-beta.N` pre-releases,
with the same signature check; the app's request carries only the word `beta`, or `stable` with `leave_beta` from a node running a beta (the one downgrade, to the newest stable release). The app only ever *checks*; applying is the updater's job, so the process that handles mesh text never holds the Docker socket.
The request to the updater carries no version or image: the updater reads GitHub itself, accepts only a strictly newer stable
`vX.Y.Z`, and runs only if cosign confirms the image was signed by this repository's release workflow for that exact tag. The new
image is referenced by digest, so what was verified is what runs. A verified backup of the settings database precedes the request.
Releases are built by `.github/workflows/release.yml` (all actions SHA-pinned; only the images job can write packages or request
a signing identity; the publish job runs none of our code). See [docs/UPDATING.md](docs/UPDATING.md) and
[docs/RELEASING.md](docs/RELEASING.md).

Wikipedia editions follow the same install path as a manual package install (disk and embedding-model checks first, resumable
download, two-pass indexing), replace only the older edition of the same package once the new one is ready, and never rebuild
the index on their own.

## Offline and privacy

At runtime the only network traffic is to the local Ollama server, plus the daily update checks (GitHub's releases API and Kiwix's
listing) which have switches in Settings. Pulling a model and downloading a Wikipedia file are buttons the user presses. No
telemetry, no analytics, no CDN assets.

## Testing approach

Pure logic (fitter, triggers, limiter) is tested directly, including multi-byte text. The engine runs with a fake
radio, a mock model, a controllable clock, and an injectable sleep. The radio adapter is driven through the real
pypubsub topics from a separate thread with a fake meshtastic interface. Downloads and model pulls use
`httpx.MockTransport`. The knowledge-base tests generate a real ZIM with `libzim` and index and search it.
