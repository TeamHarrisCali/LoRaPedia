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
   from our own node, a blocked node, a node outside a non-empty allow list, or on the wrong channel. The
   trigger rules decide whether it is a *question*, a *command* (`?help`, `?more`, `?stats`) or noise.
2. **Rate limit and queue.** Questions are checked against the per-node limit. Over the limit, a "busy, try again
   in Ns" reply is queued instead. Everything goes onto a single queue, so replies are sent one at a time.
3. **Retriever.** The question is searched in the local index (keyword + vector, fused and reranked).
4. **LLM.** The top passages and the question are sent to the model with a strict system prompt: answer only from
   the context, no markdown, "not found" if unsupported.
5. **Byte fitter.** The answer is cleaned and forced to fit the byte limit (see below).
6. **Sender.** The reply goes to the original sender (a direct message, or a channel message prefixed `@Name`)
   with the configured hop limit, with a pause between parts.

If nothing relevant is retrieved, the LLM is not called at all and the reply is "Not found in my offline library."

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
| `system.py`, `demo.py`, `sample.py` | Hardware detection; demo data; a tiny generated ZIM. |
| `api.py` | HTTP and WebSocket routes, wiring, static UI serving. |

The radio, LLM, embedder and retriever are each hidden behind a small interface, which is what lets the
simulator, the mock model, and a fake radio stand in for real ones in tests and demos.

## The byte-fitting pipeline

The 233-byte Meshtastic limit is a hard physical constraint, so fitting is built to **never** exceed it,
regardless of what the model does.

```
  budget = limit - len("@Name ")            (channel replies only)
         - len(" ?more")                    (only when parts wait for ?more)
  total  = parts_allowed * (budget - len("(3/3) "))     (1 part: just budget)

  1. Prompt the LLM: "at most <budget> bytes; only if essential, up to <total>"
  2. clean:  strip markdown, fold smart quotes to ASCII, collapse whitespace
  3. over <total> bytes?  ask the LLM ONCE to rewrite within <total>
  4. split:  fits in one message -> send as is
             else wrap at word boundaries into "(i/n) " parts, at most <parts_allowed>;
             overflow is truncated at a word boundary and ends with "..."
  5. append "[wp:Title]" to the last part only if it still fits
```

Lengths are always UTF-8 **bytes**. Truncation never splits a multi-byte character, and a single word longer
than a part is hard-split. Step 4 is the guarantee; steps 1 and 3 only improve quality. The radio adapter also
refuses to send anything over 233 bytes, as a last line of defence.

## Knowledge base

**Indexing.** `ZimReader` streams HTML entries (skipping redirects and non-HTML). `chunk_article` strips noise
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

**Retrieval.** Keyword (FTS5/BM25) and vector (nearest neighbours) each return 20 candidates. They are fused with
reciprocal rank fusion, then reranked by title match, query-term coverage and a small lead-chunk bonus. A
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
  zim/             Wikipedia files (and *.part while downloading)
```

## Frontend

React + TypeScript + Tailwind, built by Vite into static files that the backend serves, so there is one thing
to run. Fonts and icons are bundled; no request leaves the machine. A WebSocket pushes new questions and radio
status; everything else is polled REST. Settings pages edit a local draft and save only the fields you touched, so
immediate actions elsewhere (like blocking a node) are never overwritten by a stale form.

## Offline and privacy

At runtime the only network traffic is to the local Ollama server. The two things that go online, pulling a model
and downloading a Wikipedia file, are buttons the user presses. No telemetry, no analytics, no CDN assets.

## Testing approach

Pure logic (fitter, triggers, limiter) is tested directly, including multi-byte text. The engine runs with a fake
radio, a mock model, a controllable clock, and an injectable sleep. The radio adapter is driven through the real
pypubsub topics from a separate thread with a fake meshtastic interface. Downloads and model pulls use
`httpx.MockTransport`. The knowledge-base tests generate a real ZIM with `libzim` and index and search it.
