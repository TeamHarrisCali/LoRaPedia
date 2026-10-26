"""Seed/demo data so the UI looks alive in simulator mode (never touches a real radio)."""

import random
import time

from .store import Store

# (sender id, short name, question, answer, source title, latency s). Answers are real sample-corpus facts.
DEMO = [
    ("!a1b2c3d4", "BOB", "what is a quasar?",
     "A quasar is an extremely luminous active galactic nucleus powered by a supermassive black hole. [wp:Quasar]", "Quasar", 2.9),
    ("!7f3e9a10", "ANNA", "how long to boil water at altitude",
     "At high altitude, boil water for three minutes. [wp:Water purification]", "Water purification", 2.1),
    ("!0c44d2e8", "TREK", "who first climbed everest",
     "Edmund Hillary and Tenzing Norgay, on 29 May 1953. [wp:Mount Everest]", "Mount Everest", 2.4),
    ("!a1b2c3d4", "BOB", "how big can a meshtastic message be",
     "A single Meshtastic text message is limited to roughly 233 bytes. [wp:Meshtastic]", "Meshtastic", 1.8),
    ("!5d09b6c1", "FERN", "why are there two tides a day",
     "Tides come from the Moon and Sun's gravity and Earth's rotation; most coasts see two high tides daily. [wp:Tide]", "Tide", 3.2),
    ("!7f3e9a10", "ANNA", "what does insulin do",
     "Insulin regulates carbohydrate, fat and protein metabolism by promoting glucose uptake from the blood. [wp:Insulin]", "Insulin", 2.6),
    ("!0c44d2e8", "TREK", "what frequency does lora use",
     "LoRa commonly uses the 868 MHz band in Europe and the 915 MHz band in the Americas. [wp:LoRa]", "LoRa", 2.0),
    ("!91ab33f0", "JAY", "how do solar panels work",
     "Solar panels convert sunlight into electricity using photovoltaic cells, usually crystalline silicon. [wp:Solar panel]", "Solar panel", 3.5),
    ("!5d09b6c1", "FERN", "do bees dance",
     "Yes. Honey bees communicate where food is with a waggle dance. [wp:Honey bee]", "Honey bee", 1.9),
    ("!91ab33f0", "JAY", "what is photosynthesis",
     "Plants, algae and some bacteria convert light energy into chemical energy, using CO2 and water and releasing oxygen. [wp:Photosynthesis]", "Photosynthesis", 2.7),
    ("!a1b2c3d4", "BOB", "capital of atlantis",
     "Not found in my offline library.", "", 1.1),
    ("!0c44d2e8", "TREK", "what is a black hole",
     "(1/2) A region of spacetime where gravity is so strong that nothing, not even light, can escape once past the event horizon.",
     "Black hole", 3.8),
]


def seed(store: Store, n: int = 36, rng: random.Random | None = None) -> int:
    """Insert `n` demo queries spread over the last 24 hours (busier in the daytime). Returns how many."""
    rng = rng or random.Random(7)
    now = time.time()
    for i in range(n):
        sender_id, name, q, a, source, latency = DEMO[i % len(DEMO)]
        hour_ago = rng.betavariate(2, 3) * 24  # skew toward recent hours so the sparkline has a shape
        parts = a.split("\n")
        qid = store.log_query(
            sender_id=sender_id, sender_name=name, question=q, parts=parts, byte_limit=200,
            latency_s=round(latency + rng.uniform(-0.4, 0.6), 2), sources=[source] if source else [],
            debug={"prompt": f"(demo data)\nContext:\n[1] {source}: ...\n\nQuestion: {q}", "compressed": False,
                   "chunks": [{"title": source, "text": "Demo chunk text.", "score": 2.4}] if source else [],
                   "demo": True})
        with store._lock:
            store._db.execute("UPDATE queries SET ts=? WHERE id=?", (now - hour_ago * 3600, qid))
            store._db.commit()
    return n
