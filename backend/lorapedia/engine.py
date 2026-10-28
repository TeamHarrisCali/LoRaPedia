"""Message flow: Radio listener -> Queue -> Retriever -> LLM -> Byte fitter -> Sender."""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from . import fitter
from .config import Settings
from .llm.base import LLM
from .radio.base import Packet, Radio
from .ratelimit import RateLimiter
from .retrieval.base import Retriever
from .sources import order_sources
from .store import Store
from .triggers import parse_message

log = logging.getLogger(__name__)

NOT_FOUND = "Not found in my offline library."
NOT_FOUND_PARTIAL = "Not found yet: my library is still being indexed. Try again later."
# Added by code, not left to the model, so the warning can never be forgotten or reworded away.
FALLBACK_PREFIX = "[AI memory - may be wrong] "
FALLBACK_SYSTEM = (
    "The offline encyclopedia has nothing on this question, so answer from your own general knowledge. "
    "Be brief and factual, plain text, no markdown, no preamble. If you are not reasonably sure, reply exactly: "
    "I don't know. For medical, legal, electrical or other safety-critical questions give only basic general "
    "information and say to ask a professional."
)
HELP_TEXT = "Ask me a question by DM. Commands: ?help ?more ?stats ?ping. Answers come from offline Wikipedia."
MORE_HINT = " ?more"
CHUNK_CHARS = 700  # per-chunk context cap so small models stay fast


@dataclass
class Generated:
    parts: list[str]
    chunks: list
    prompt: str
    compressed: bool
    raw: str  # cleaned LLM text before fitting
    fallback: bool = False  # answered from the model's own memory because the library had nothing
    found: bool = True  # the answer came from retrieved Wikipedia text
    sources: list[str] = field(default_factory=list)  # article titles behind it, best first; for the web panel only


@dataclass
class Job:
    packet: Packet
    question: str | None = None  # None = canned reply only
    reply: str | None = None


class Engine:
    def __init__(
        self,
        *,
        settings: Callable[[], Settings],
        radio: Radio,
        sim_radio: Radio | None = None,
        llm: LLM,
        retriever: Retriever,
        store: Store,
        emit: Callable[[dict], Awaitable[None]] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        library_state: Callable[[], str] = lambda: "none",
    ) -> None:
        # "complete"/"covered": every article is searchable, so "no match" is definitive. "partial": still indexing.
        self._library_state = library_state
        self._settings = settings
        self.radio, self.llm, self.retriever, self.store = radio, llm, retriever, store
        self.sim_radio = sim_radio  # simulated senders are answered here, never over a real radio
        self._emit = emit
        self._clock, self._sleep = clock, sleep
        self.limiter = RateLimiter(clock)
        self.queue: asyncio.Queue[Job] = asyncio.Queue()
        self._more: dict[str, list[str]] = {}  # sender -> parts not yet sent
        self._delayed: set[asyncio.Task] = set()  # questions waiting out their node's cooldown

    # ---- intake -------------------------------------------------------------------------

    @property
    def pending_count(self) -> int:
        """Everything not yet answered: in the queue, being processed, or waiting out a cooldown."""
        return self.queue.qsize() + len(self._delayed)

    async def submit(self, packet: Packet) -> str:
        """Filter, rate-limit and enqueue. Returns the outcome (handy for tests/UI)."""
        s = self._settings()
        if packet.sender_id == self.radio.node_id or packet.sender_id in s.blocklist:
            return "ignored"
        if s.allowlist and packet.sender_id not in s.allowlist:
            return "ignored"
        if not packet.is_dm and packet.channel != s.channel_index:
            return "ignored"  # channel triggers only apply on the configured channel
        parsed = parse_message(packet.text, packet.is_dm, s.trigger_mode, s.trigger_prefixes)
        if parsed.kind == "ignore":
            return "ignored"

        if parsed.kind == "command":
            self.queue.put_nowait(Job(packet, reply=self._command_reply(parsed.text, packet, s)))
            return "command"

        if self.pending_count >= s.queue_max:
            self.queue.put_nowait(Job(packet, reply="Busy, try again later."))
            return "queue_full"

        node = packet.sender_id
        if s.queue_when_limited and s.rate_limit_queries > 0:
            waiting = self.limiter.pending(node)
            if waiting >= s.max_queued_per_node:  # a cap keeps one node from filling the whole queue
                self.queue.put_nowait(Job(packet, reply=f"Busy: {waiting} of your questions are already waiting."))
                return "rate_limited"
            wait = self.limiter.reserve(node, s.rate_limit_queries, s.rate_limit_window_s)
            if wait:
                job = Job(packet, question=parsed.text)
                task = asyncio.create_task(self._release(job, wait))
                self._delayed.add(task)
                task.add_done_callback(self._delayed.discard)
                if s.ack_queued:
                    self.queue.put_nowait(Job(packet, reply=f"Queued. Answer in about {wait}s."))
                return "delayed"
        else:
            wait = self.limiter.check(node, s.rate_limit_queries, s.rate_limit_window_s)
            if wait:
                self.queue.put_nowait(Job(packet, reply=f"Busy, try again in {wait}s."))
                return "rate_limited"
        self.queue.put_nowait(Job(packet, question=parsed.text))
        return "queued"

    async def _release(self, job: Job, wait: float) -> None:
        """Hold a question until its node's cooldown is over, then let it join the queue."""
        await self._sleep(wait)
        self.queue.put_nowait(job)

    @staticmethod
    def _ping_reply(packet: Packet) -> str:
        """A range test: how the asker's own message reached this radio. Needs no model and no library."""
        if packet.snr is None and packet.rssi is None and packet.hops is None:
            return "pong! (no signal information for this message)"
        signal = ", ".join(x for x in (f"SNR {packet.snr:g} dB" if packet.snr is not None else "",
                                       f"RSSI {packet.rssi} dBm" if packet.rssi is not None else "") if x)
        if packet.hops == 0:
            return f"pong! Heard you directly: {signal}."
        if packet.hops:
            relays = f"{packet.hops} relay" + ("s" if packet.hops != 1 else "")
            return f"pong! Passed through {relays}; the last hop was {signal}." if signal else f"pong! Passed through {relays}."
        return f"pong! Signal: {signal}."  # hop count unknown (older firmware)

    def _command_reply(self, name: str, packet: Packet, s: Settings) -> str:
        if name == "help":
            return HELP_TEXT
        if name == "ping":
            return self._ping_reply(packet)
        if name == "stats":
            st = self.store.today_stats()
            return f"{st['queries']} Qs today, avg {st['avg_latency_s']}s, queue {self.pending_count}."
        # "more": release the next held part (only used when auto_send_parts is off)
        held = self._more.get(packet.sender_id)
        if not held:
            return "Nothing more to send."
        part = held.pop(0)
        if not held:
            del self._more[packet.sender_id]
        return part

    # ---- worker -------------------------------------------------------------------------

    async def run_worker(self) -> None:
        while True:
            job = await self.queue.get()
            try:
                await self.process(job)
            except Exception as exc:  # never let one bad query kill the worker
                await self._send(job.packet, ["Error, try again later."], self._settings())
                await self._publish({"type": "error", "detail": repr(exc)})
            finally:
                self.queue.task_done()

    async def process(self, job: Job) -> None:
        s = self._settings()
        if job.question is None:
            # Canned replies (help/stats/busy/more) are clamped too: the byte limit is a hard guarantee.
            room = s.byte_limit - self._reply_prefix_bytes(job.packet)
            await self._send(job.packet, [fitter.truncate_bytes(job.reply or "", room)], s)
            return

        started = self._clock()
        gen = await self.generate(job.question, s, self._reply_prefix_bytes(job.packet))
        parts, chunks, prompt, compressed = gen.parts, gen.chunks, gen.prompt, gen.compressed

        await self._send(job.packet, parts, s)
        latency = round(self._clock() - started, 2)
        entry = dict(
            sender_id=job.packet.sender_id, sender_name=job.packet.sender_name, question=job.question,
            parts=parts, byte_limit=s.byte_limit, latency_s=latency, sources=gen.sources,  # unique, best first; none for "not found"
            debug={"prompt": prompt, "compressed": compressed, "fallback": gen.fallback,
                   "chunks": [{"title": c.title, "text": c.text, "score": c.score} for c in chunks]},
        )
        entry["id"] = self.store.log_query(**entry)
        await self._publish({"type": "query", **entry})

    async def generate(self, question: str, s: Settings, prefix_bytes: int = 0) -> "Generated":
        """Retrieve, ask the LLM, and fit to the byte budget. Sends nothing (also used by the UI "try it" box)."""
        limit = s.byte_limit - prefix_bytes
        max_parts = s.effective_max_parts
        if max_parts > 1 and not s.auto_send_parts:
            limit -= fitter.byte_len(MORE_HINT)  # keep room for the "?more" hint on part 1
        budget = fitter.total_budget(limit, max_parts)

        chunks = await self.retriever.search(question, s.top_k)
        prompt = self._build_prompt(question, chunks, limit, budget)
        raw = ""
        compressed = False
        if chunks:
            raw = fitter.clean_text(
                await self.llm.generate(s.system_prompt, prompt, temperature=s.temperature, max_tokens=s.max_tokens)
            )
            if fitter.byte_len(raw) > budget:  # step 2: ask the LLM to compress once
                compressed = True
                raw = fitter.clean_text(await self.llm.generate(
                    s.system_prompt,
                    f"REWRITE this answer in at most {budget} bytes, keeping the key facts:\n{raw}",
                    temperature=s.temperature, max_tokens=s.max_tokens))
        found = bool(chunks) and not raw.lower().startswith("not found")
        fallback = False
        if not found:
            state = self._library_state()
            if state in ("complete", "covered") and s.ai_fallback:
                # Every article is searchable, so "not in any article" is certain: the model may speak from memory, marked as a guess.
                guess = await self._ai_memory(question, s, limit)  # one message at most: see below
                raw = FALLBACK_PREFIX + guess if guess else NOT_FOUND
                fallback = bool(guess)
            else:
                raw = NOT_FOUND_PARTIAL if state == "partial" else NOT_FOUND

        # step 3: guaranteed-safe truncate + split (never exceeds the limit)
        # A guess is the least reliable thing we send, so it never uses more than one message of airtime.
        parts = fitter.fit_answer(raw, limit, 1 if fallback else max_parts)
        return Generated(parts, chunks, prompt, compressed, raw, fallback, found, order_sources(raw, chunks) if found else [])

    async def _ai_memory(self, question: str, s: Settings, budget: int) -> str | None:
        """A short answer from the model's own knowledge, or None if it does not know."""
        room = budget - fitter.byte_len(FALLBACK_PREFIX)
        if room < 20:
            return None
        try:
            text = fitter.clean_text(await self.llm.generate(
                FALLBACK_SYSTEM, f"Question: {question}\n\nAnswer in at most {room} bytes.",
                temperature=s.temperature, max_tokens=s.max_tokens))
        except Exception:
            log.warning("AI-memory fallback failed", exc_info=True)  # the library lookup already worked; just say not found
            return None
        refusals = ("i don't know", "i do not know", "not found", "i'm not sure", "i am not sure", "i cannot", "i can't")
        return None if not text or text.lower().startswith(refusals) else text

    def _build_prompt(self, question: str, chunks, limit: int, budget: int) -> str:
        ctx = "\n".join(f"[{i}] {c.title}: {c.text[:CHUNK_CHARS]}" for i, c in enumerate(chunks, 1))
        size = f"Answer in at most {limit} bytes."
        if budget > limit:
            size += f" Only if essential, you may use up to {budget} bytes."
        return f"Context:\n{ctx}\n\nQuestion: {question}\n\n{size}"

    # ---- sending ------------------------------------------------------------------------

    def _reply_prefix(self, packet: Packet) -> str:
        return "" if packet.is_dm else f"@{packet.sender_name} "

    def _reply_prefix_bytes(self, packet: Packet) -> int:
        return fitter.byte_len(self._reply_prefix(packet))

    async def _send(self, packet: Packet, parts: list[str], s: Settings) -> None:
        to = packet.sender_id if packet.is_dm else None
        if to is None and s.trigger_mode == "dm":
            # Hard guarantee for direct-message-only mode: nothing is ever posted to a channel,
            # even if a channel packet somehow reached the worker (e.g. the setting changed while it was queued).
            log.warning("dropping a public reply: trigger mode is direct-message only")
            return
        prefix = self._reply_prefix(packet)
        send_now = parts
        if not s.auto_send_parts and len(parts) > 1:
            # Hold the rest for "?more"; hint at it only if the hint fits in the first part.
            self._more[packet.sender_id] = parts[1:]
            send_now = [parts[0] + MORE_HINT]  # room was reserved in process()
        for i, part in enumerate(send_now):
            if i:
                await self._sleep(s.send_delay_s)  # respect LoRa airtime between parts
            radio = self.sim_radio if packet.simulated and self.sim_radio else self.radio
            await radio.send_text(prefix + part, to, packet.channel, s.hop_limit)

    async def _publish(self, event: dict) -> None:
        if self._emit:
            await self._emit(event)
