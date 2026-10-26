"""Tiny sample ZIM (written with libzim) for tests, demos and the simulator.

The HTML mimics Wikipedia's structure (infobox, hatnote, citations, References
and See also sections) so the extractor and chunker are exercised on realistic markup.
Run `python -m lorapedia.sample data/zim/sample.zim` to create one.
"""

import sys
from pathlib import Path

from libzim.writer import Creator, Hint, Item, StringProvider

# (title, lead paragraphs, [(section heading, paragraphs)])
ARTICLES: list[tuple[str, list[str], list[tuple[str, list[str]]]]] = [
    ("Quasar",
     ["A quasar is an extremely luminous active galactic nucleus powered by a supermassive black hole "
      "that is accreting matter. Quasars can outshine all the stars of their host galaxy combined.",
      "The first quasars were identified in the early 1960s as radio sources with starlike optical counterparts."],
     [("Properties", ["Quasars emit across the whole electromagnetic spectrum. Their light is strongly redshifted "
                      "because they lie billions of light years away."]),
      ("Discovery", ["Maarten Schmidt recognised the large redshift of the radio source 3C 273 in 1963."])]),
    ("Photosynthesis",
     ["Photosynthesis is the process by which plants, algae and some bacteria convert light energy into "
      "chemical energy stored in sugars. It consumes carbon dioxide and water and releases oxygen."],
     [("Light reactions", ["In the light reactions, chlorophyll in the thylakoid membranes absorbs light and "
                           "drives the production of ATP and NADPH."]),
      ("Calvin cycle", ["The Calvin cycle uses ATP and NADPH to fix carbon dioxide into sugars in the stroma."])]),
    ("Meshtastic",
     ["Meshtastic is an open-source project that builds off-grid mesh networks using inexpensive LoRa radios. "
      "Messages hop from node to node, so devices can communicate without cellular coverage."],
     [("Message size", ["A single Meshtastic text message is limited to roughly 233 bytes."])]),
    ("LoRa",
     ["LoRa is a proprietary radio modulation technique derived from chirp spread spectrum. It trades data "
      "rate for range, reaching several kilometres on very little power."],
     [("Frequencies", ["LoRa devices commonly use the 868 MHz band in Europe and the 915 MHz band in the "
                       "Americas."])]),
    ("Water purification",
     ["Water purification removes contaminants to make water safe to drink. Boiling water for one minute "
      "kills most disease-causing microbes; at high altitude boil it for three minutes."],
     [("Filtration", ["Ceramic and membrane filters remove bacteria and protozoa but not all viruses."]),
      ("Chemical treatment", ["Chlorine dioxide tablets are an effective field treatment."])]),
    ("Mount Everest",
     ["Mount Everest is Earth's highest mountain above sea level, located in the Himalayas on the border "
      "between Nepal and Tibet. Its summit is 8,849 metres above sea level."],
     [("First ascent", ["Edmund Hillary and Tenzing Norgay made the first confirmed ascent on 29 May 1953."])]),
    ("Honey bee",
     ["Honey bees are social insects known for building perennial colonies and producing honey and beeswax. "
      "A colony may hold tens of thousands of workers and a single queen."],
     [("Communication", ["Bees communicate the location of food with a waggle dance."])]),
    ("Black hole",
     ["A black hole is a region of spacetime where gravity is so strong that nothing, not even light, can "
      "escape once past the event horizon."],
     [("Formation", ["Stellar black holes form when massive stars collapse at the end of their lives."])]),
    ("Café",
     ["A café is an establishment that primarily serves coffee, tea and light meals. The name is "
      "borrowed from French."],
     []),
    ("Insulin",
     ["Insulin is a peptide hormone made by beta cells of the pancreatic islets. It regulates the "
      "metabolism of carbohydrates, fats and protein by promoting the absorption of glucose from the blood."],
     [("Medical use", ["People with type 1 diabetes depend on insulin injections to control blood sugar."])]),
    ("Tide",
     ["Tides are the rise and fall of sea levels caused by the combined gravitational forces of the Moon and "
      "the Sun and by the rotation of the Earth. Most coasts see two high tides a day."],
     [("Spring and neap tides", ["Spring tides, with the greatest range, occur near new and full moon."])]),
    ("Solar panel",
     ["A solar panel converts sunlight into electricity using photovoltaic cells, most commonly made of "
      "crystalline silicon. Typical panels turn roughly 15 to 22 percent of sunlight into power."],
     [("Off-grid use", ["Panels paired with a charge controller and battery can run radios and lights "
                        "far from any power grid."])]),
]


def render_html(title: str, lead: list[str], sections: list[tuple[str, list[str]]]) -> str:
    body = [f"<h1>{title}</h1>", f'<div class="hatnote">For other uses, see {title} (disambiguation).</div>',
            '<table class="infobox"><tr><td>Infobox junk that must not be indexed</td></tr></table>']
    body += [f"<p>{p}<sup class=\"reference\">[1]</sup></p>" for p in lead]
    for heading, paras in sections:
        body.append(f"<h2>{heading}</h2>")
        body += [f"<p>{p}</p>" for p in paras]
    body += ["<h2>See also</h2><ul><li>Related page that must not be indexed</li></ul>",
             "<h2>References</h2><ol><li>Citation text that must not be indexed</li></ol>"]
    return f"<!DOCTYPE html><html><head><title>{title}</title><style>p{{}}</style></head><body>{''.join(body)}</body></html>"


class _Article(Item):
    def __init__(self, title: str, html: str) -> None:
        super().__init__()
        self._title, self._html = title, html

    def get_path(self) -> str:
        return self._title.replace(" ", "_")

    def get_title(self) -> str:
        return self._title

    def get_mimetype(self) -> str:
        return "text/html"

    def get_contentprovider(self) -> StringProvider:
        return StringProvider(self._html)

    def get_hints(self) -> dict:
        return {Hint.FRONT_ARTICLE: True}


def make_sample_zim(path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    with Creator(str(path)).config_indexing(False, "eng") as creator:
        creator.set_mainpath(ARTICLES[0][0].replace(" ", "_"))
        for title, lead, sections in ARTICLES:
            creator.add_item(_Article(title, render_html(title, lead, sections)))
        for key, value in (("Title", "LoRaPedia sample"), ("Language", "eng"), ("Creator", "LoRaPedia"),
                           ("Publisher", "LoRaPedia"), ("Description", "Sample articles"),
                           ("Name", "lorapedia_sample"), ("Date", "2026-01-01")):
            creator.add_metadata(key, value)
    return path


if __name__ == "__main__":
    print(make_sample_zim(sys.argv[1] if len(sys.argv) > 1 else "sample.zim"))
