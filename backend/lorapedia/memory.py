"""A short memory of each person's last few exchanges, so a follow-up ("and its population?") can be understood.

It lives in RAM only: nothing is written to disk, nothing goes in a backup, and a restart forgets everyone. It is kept per sender and is
never shared: one node's questions are never shown to the model while answering another's. A person is forgotten after a period of
silence (Settings → Mesh AI, 24 h by default; 0 turns the memory off) or when they send ?reset.

Only answers found in the offline library are remembered. Answers about the mesh are not (they hold names chosen by strangers, which
must never reach the model), and neither are guesses from the model's own memory.
"""
import re
import time
from collections import OrderedDict, deque
from typing import Callable

MAX_EXCHANGES = 3        # per person
MAX_PEOPLE = 200         # the least recently active are dropped first
ANSWER_CHARS = 300       # of each remembered answer; the prompt must stay small for a 3B model
QUESTION_CHARS = 200

_REFERS_BACK = frozenset("it its he him his she her they them their theirs that this those these there then".split())
_OPENERS = ("and ", "what about", "how about", "what else", "tell me more", "more about", "why", "how come", "go on")


def is_follow_up(question: str) -> bool:
    """Does this question lean on an earlier one? A cheap guess from its wording: a short question with a pronoun or an "and ..." opener."""
    words = re.findall(r"[a-z']+", question.lower())
    if not words or len(words) > 10:
        return False
    text = " ".join(words)
    return text.startswith(_OPENERS) or bool(_REFERS_BACK.intersection(words))


class ConversationMemory:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._people: OrderedDict[str, tuple[float, deque]] = OrderedDict()   # sender -> (last active, [(question, answer), ...])

    def recall(self, sender: str, ttl_s: float) -> list[tuple[str, str]]:
        """The person's recent exchanges, oldest first; empty if there are none, or they have been silent for `ttl_s`."""
        entry = self._people.get(sender)
        if entry is None or ttl_s <= 0:
            return []
        if self._clock() - entry[0] > ttl_s:
            del self._people[sender]
            return []
        return list(entry[1])

    def remember(self, sender: str, question: str, answer: str, ttl_s: float) -> None:
        if ttl_s <= 0 or not sender:
            return
        entry = self._people.pop(sender, None)
        if entry is None or self._clock() - entry[0] > ttl_s:
            entry = (0.0, deque(maxlen=MAX_EXCHANGES))
        entry[1].append((question[:QUESTION_CHARS], answer[:ANSWER_CHARS]))
        self._people[sender] = (self._clock(), entry[1])
        while len(self._people) > MAX_PEOPLE:
            self._people.popitem(last=False)

    def forget(self, sender: str) -> bool:
        return self._people.pop(sender, None) is not None

    def clear(self) -> int:
        n = len(self._people)
        self._people.clear()
        return n

    def __len__(self) -> int:
        return len(self._people)
