"""Catch a test that connects a radio and never closes it.

pypubsub's topics are global and hold listeners weakly. A radio left subscribed is garbage collected some time later (when depends on the
Python version), and until its listener is cleaned up, whichever test publishes on the topic next fails with "Dead Listener called".
This remembers every MeshtasticRadio a test creates and reports the ones still subscribed, so the leak is named at the test that made it.
"""
from pubsub import pub

from lorapedia.radio.meshtastic_radio import MeshtasticRadio

TOPICS = ("meshtastic.receive.text", "meshtastic.receive", "meshtastic.connection.lost")


class RadioWatch:
    def __init__(self, monkeypatch) -> None:
        self.radios: list[MeshtasticRadio] = []  # strong references: a radio under watch cannot be collected while we look
        original, watch = MeshtasticRadio.__init__, self

        def init(radio, *args, **kwargs):
            original(radio, *args, **kwargs)
            watch.radios.append(radio)

        monkeypatch.setattr(MeshtasticRadio, "__init__", init)

    def still_subscribed(self) -> list[str]:
        found = []
        for topic in TOPICS:
            try:
                listeners = pub.getDefaultTopicMgr().getTopic(topic).getListeners()
            except Exception:
                continue  # the topic was never created: nobody subscribed
            for listener in listeners:
                owner = getattr(listener.getCallable(), "__self__", None)
                if any(owner is radio for radio in self.radios):
                    found.append(topic)
        return found
