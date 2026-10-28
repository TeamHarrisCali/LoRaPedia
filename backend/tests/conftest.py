"""Shared test setup.

Every MeshtasticRadio a test creates is unsubscribed from pypubsub's global topics when the test ends, whether or not the test closed it.
The topics hold listeners weakly: a radio left subscribed is garbage collected some time later (when depends on the Python version) and,
until its listener has been cleaned up, whichever test publishes on the topic next fails with "Dead Listener called, still subscribed!".
That showed up as a flaky test_radio.py on Python 3.11 and 3.12 in CI. Holding the radios until teardown means their listeners are alive
when they are removed, so no test can leave one behind for the next.
"""
import pytest
from pubsub import pub

from lorapedia.radio.meshtastic_radio import MeshtasticRadio


@pytest.fixture(autouse=True)
def unsubscribe_radios_the_test_left_open(monkeypatch):
    radios: list[MeshtasticRadio] = []
    original = MeshtasticRadio.__init__

    def init(radio, *args, **kwargs):
        original(radio, *args, **kwargs)
        radios.append(radio)

    monkeypatch.setattr(MeshtasticRadio, "__init__", init)
    yield
    for radio in radios:
        for listener, topic in ((radio._on_text, "meshtastic.receive.text"), (radio._on_any, "meshtastic.receive"),
                                (radio._on_connection_lost, "meshtastic.connection.lost")):
            try:
                pub.unsubscribe(listener, topic)
            except Exception:
                pass  # already unsubscribed by close(), or never subscribed
