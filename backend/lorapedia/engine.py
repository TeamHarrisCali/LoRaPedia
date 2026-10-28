"""Message flow: Radio listener -> Queue -> Retriever -> LLM -> Byte fitter -> Sender."""

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from . import fitter
from .config import Settings
from .llm.base import LLM
from .memory import ConversationMemory, is_follow_up
from .meshask import MeshAsker
from .radio.base import Packet, Radio
from .ratelimit import RateLimiter
from .retrieval.base import Retriever
from .sources import order_sources
from .store import Store, summarize_delivery
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
HELP_TEXT = "Ask by DM. Offline Wikipedia, or the mesh: nodes nearby, battery, temperature. ?help ?more ?stats ?ping ?reset (forget our chat)"
MORE_HINT = " ?more"
CHUNK_CHARS = 700  # per-chunk context cap so small models stay fast
DATE_WORDS = re.compile(r"\b(?:today|tonight|now|currently|current|this (?:year|month|week)|how old|years? ago|how long (?:ago|until|since)|until|since)\b", re.I)


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
    mesh: bool = False  # answered from the mesh database by a fixed lookup: no model, no library


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
        mesh_ask: MeshAsker | None = None,
        operator: Callable[[Packet, Settings], Awaitable[bool]] | None = None,
        wall: Callable[[], float] = time.time,
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
        self.mesh_ask = mesh_ask  # questions about the mesh itself, answered without the model
        # Hands a message that belongs to one of the operator's chats to the Messages page (True = it was taken). All the engine ever gets
        # of operator messaging: a way to step aside, never a way to send.
        self._operator = operator
        # The daily limit: questions each node has asked today. Loaded from the question log the first time a node is seen each day, so a
        # restart does not hand everyone a fresh allowance.
        self._wall = wall
        self._quota_day = ""
        self._quota: dict[str, int] = {}
        self._notified: set[str] = set()  # told today that the limit is reached (told once: more would cost airtime too)
        self.memory = ConversationMemory(clock)  # each sender's last few answered questions, for follow-ups; RAM only

    # ---- intake -------------------------------------------------------------------------

    @property
    def pending_count(self) -> int:
        """Everything not yet answered: in the queue, being processed, or waiting out a cooldown."""
        return self.queue.qsize() + len(self._delayed)

    async def submit(self, packet: Packet) -> str:
        """Filter, rate-limit and enqueue. Returns the outcome (handy for tests/UI)."""
        s = self._settings()
        if not packet.is_dm:
            return "ignored"  # LoRaPedia never answers in a public channel; only a direct message can be a question
        if packet.sender_id == self.radio.node_id or packet.sender_id in s.blocklist:
            return "ignored"
        if s.allowlist and packet.sender_id not in s.allowlist:
            return "ignored"
        parsed = parse_message(packet.text, packet.is_dm, s.trigger_prefixes)
        if parsed.kind == "ignore":
            return "ignored"

        if parsed.kind == "command":
            self.queue.put_nowait(Job(packet, reply=self._command_reply(parsed.text, packet, s)))
            return "command"

        if self._operator is not None and await self._operator(packet, s):
            return "operator"  # part of a chat the operator started: for them, not for the AI

        node = packet.sender_id
        cap = s.daily_cap(node)
        if cap > 0 and self._asked_today(node) >= cap:
            if node not in self._notified:
                self._notified.add(node)
                self.queue.put_nowait(Job(packet, reply=f"Daily limit reached ({cap} questions). Try again tomorrow."))
            return "daily_limit"

        if self.pending_count >= s.queue_max:
            self.queue.put_nowait(Job(packet, reply="Busy, try again later."))
            return "queue_full"

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
                self._quota[node] = self._asked_today(node) + 1
                return "delayed"
        else:
            wait = self.limiter.check(node, s.rate_limit_queries, s.rate_limit_window_s)
            if wait:
                self.queue.put_nowait(Job(packet, reply=f"Busy, try again in {wait}s."))
                return "rate_limited"
        self.queue.put_nowait(Job(packet, question=parsed.text))
        self._quota[node] = self._asked_today(node) + 1
        return "queued"

    def _midnight(self) -> float:
        t = time.localtime(self._wall())
        return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))

    def _roll_day(self) -> None:
        day = time.strftime("%Y-%m-%d", time.localtime(self._wall()))
        if day != self._quota_day:
            self._quota_day, self._quota, self._notified = day, {}, set()

    def quota_today(self) -> dict[str, int]:
        """Questions accepted today per node, for the nodes seen since this started (a question that failed is counted here but never logged)."""
        self._roll_day()
        return dict(self._quota)

    def _asked_today(self, node: str) -> int:
        """Questions this node has had accepted today (this computer's local day)."""
        self._roll_day()
        if node not in self._quota:
            self._quota[node] = self.store.question_counts(self._midnight()).get(node, 0)
        return self._quota[node]

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
        if name == "reset":
            return "OK, I have forgotten our conversation." if self.memory.forget(packet.sender_id) else "Nothing to forget."
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
            room = s.byte_limit
            await self._send(job.packet, [fitter.truncate_bytes(job.reply or "", room)], s)  # a canned reply: its delivery is tracked, but belongs to no answer
            return

        started = self._clock()
        gen = await self.generate(job.question, s, sender=job.packet.sender_id)
        parts, chunks, prompt, compressed = gen.parts, gen.chunks, gen.prompt, gen.compressed

        packet_ids = await self._send(job.packet, parts, s)
        if gen.found and not gen.fallback and not gen.mesh:
            # Only what the library answered: a mesh answer holds strangers' names, a guess is the least reliable thing we say.
            self.memory.remember(job.packet.sender_id, job.question, gen.raw, s.memory_hours * 3600)
        latency = round(self._clock() - started, 2)
        entry = dict(
            sender_id=job.packet.sender_id, sender_name=job.packet.sender_name, question=job.question,
            parts=parts, byte_limit=s.byte_limit, latency_s=latency, sources=gen.sources,  # unique, best first; none for "not found"
            debug={"prompt": prompt, "compressed": compressed, "fallback": gen.fallback, "mesh": gen.mesh,
                   "chunks": [{"title": c.title, "text": c.text, "score": c.score} for c in chunks]},
        )
        entry["id"] = self.store.log_query(**entry)
        self.store.delivery_attach(packet_ids, entry["id"])  # so the Live Feed can say whether each part arrived
        sent = self.store.deliveries_for([entry["id"]]).get(entry["id"], [])
        entry["delivery"] = {"state": summarize_delivery([p["state"] for p in sent]), "parts": sent}
        await self._publish({"type": "query", **entry})

    async def generate(self, question: str, s: Settings, prefix_bytes: int = 0, sender: str | None = None) -> "Generated":
        """Retrieve, ask the LLM, and fit to the byte budget. Sends nothing (also used by the UI "try it" box).

        `sender` lets a follow-up lean on that person's earlier questions; a question about the mesh is answered from its database instead."""
        limit = s.byte_limit - prefix_bytes
        max_parts = s.effective_max_parts
        if max_parts > 1 and not s.auto_send_parts:
            limit -= fitter.byte_len(MORE_HINT)  # keep room for the "?more" hint on part 1
        budget = fitter.total_budget(limit, max_parts)

        if self.mesh_ask is not None:
            asked = await asyncio.to_thread(self.mesh_ask.answer, question)
            if asked is not None:
                parts = fitter.fit_answer(asked, limit, max_parts)
                return Generated(parts, [], "(answered from the mesh database: no model was asked)", False, asked, mesh=True)

        history = self.memory.recall(sender, s.memory_hours * 3600) if sender and is_follow_up(question) else []
        query = f"{history[-1][0]} {question}" if history else question  # "and its population?" is searched together with what it follows
        chunks = await self.retriever.search(query, s.top_k)
        prompt = self._build_prompt(question, chunks, limit, budget, history)
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
                guess = await self._ai_memory(query, s, budget)  # up to the same number of parts as any answer
                raw = FALLBACK_PREFIX + guess if guess else NOT_FOUND
                fallback = bool(guess)
            else:
                raw = NOT_FOUND_PARTIAL if state == "partial" else NOT_FOUND

        # step 3: guaranteed-safe truncate + split (never exceeds the limit). A guess continues over several messages like any answer: cutting
        # it at one message left sentences ending in "..." (the warning is in the first part, which is the one everybody reads).
        parts = fitter.fit_answer(raw, limit, max_parts)
        return Generated(parts, chunks, prompt, compressed, raw, fallback, found, order_sources(raw, chunks) if found else [])

    async def _ai_memory(self, question: str, s: Settings, budget: int) -> str | None:
        """A short answer from the model's own knowledge, or None if it does not know. `budget` is all the bytes the parts may hold."""
        room = budget - fitter.byte_len(FALLBACK_PREFIX)
        if room < 20:
            return None
        try:
            text = fitter.clean_text(await self.llm.generate(
                FALLBACK_SYSTEM, f"Question: {question}\n\nAnswer in at most {room} bytes.",
                temperature=s.temperature, max_tokens=s.max_tokens))
            if fitter.byte_len(text) > room:  # a small model overshoots: ask once for a shorter version, like an answer from the library
                shorter = fitter.clean_text(await self.llm.generate(
                    FALLBACK_SYSTEM, f"REWRITE this answer in at most {room} bytes, keeping the key facts:\n{text}",
                    temperature=s.temperature, max_tokens=s.max_tokens))
                text = shorter or text
        except Exception:
            log.warning("AI-memory fallback failed", exc_info=True)  # the library lookup already worked; just say not found
            return None
        refusals = ("i don't know", "i do not know", "not found", "i'm not sure", "i am not sure", "i cannot", "i can't")
        return None if not text or text.lower().startswith(refusals) else text

    def _build_prompt(self, question: str, chunks, limit: int, budget: int, history: list[tuple[str, str]] | None = None) -> str:
        ctx = "\n".join(f"[{i}] {c.title}: {c.text[:CHUNK_CHARS]}" for i, c in enumerate(chunks, 1))
        size = f"Answer in at most {limit} bytes."
        if budget > limit:
            size += f" Only if essential, you may use up to {budget} bytes."
        earlier = ""
        if history:  # the same person's, never anyone else's
            earlier = ("Earlier in this conversation (only to tell what words like \"it\" or \"that\" refer to):\n"
                       + "\n".join(f"Q: {q}\nA: {a}" for q, a in history) + "\n\n")
        today = f"Today's date: {time.strftime('%A %d %B %Y')}.\n" if DATE_WORDS.search(question) else ""  # a live fact, only for questions that need it
        return f"Context:\n{ctx}\n\n{earlier}{today}Question: {question}\n\n{size}"

    # ---- sending ------------------------------------------------------------------------

    async def _send(self, packet: Packet, parts: list[str], s: Settings) -> list[int]:
        """Send each part to the person who asked. Returns the packet ids of the parts a real radio sent (what their acknowledgements name)."""
        if not packet.is_dm:
            # The guarantee, second layer (submit() already ignores public messages): a reply is only ever addressed to the person
            # who asked, so even a public packet that somehow reached the worker gets nothing posted to the channel.
            log.warning("not replying to a public message: LoRaPedia answers direct messages only")
            return []
        to = packet.sender_id
        send_now = parts
        if not s.auto_send_parts and len(parts) > 1:
            # Hold the rest for "?more"; hint at it only if the hint fits in the first part.
            self._more[packet.sender_id] = parts[1:]
            send_now = [parts[0] + MORE_HINT]  # room was reserved in process()
        ids: list[int] = []
        for i, part in enumerate(send_now):
            if i:
                await self._sleep(s.send_delay_s)  # respect LoRa airtime between parts
            radio = self.sim_radio if packet.simulated and self.sim_radio else self.radio
            packet_id = await radio.send_text(part, to, packet.channel, s.hop_limit)
            if packet_id is not None:
                self.store.delivery_sent(packet_id, to, part=i)
                ids.append(packet_id)
        return ids

    async def _publish(self, event: dict) -> None:
        if self._emit:
            await self._emit(event)
