# Changelog

All notable changes to LoRaPedia. The format follows [Keep a Changelog](https://keepachangelog.com/), and versions follow
[Semantic Versioning](https://semver.org/). While the version is 0.x, anything may change between releases.

## [Unreleased]

## [0.5.0-beta.1] - 2026-10-29

A test version for the beta channel.

### Changed
- **The Indexing tab on the Knowledge page shows its progress from any tab.** Indexing moved to its own tab in 0.4.0, so starting it from the
  Library tab left no sign it was running. The tab now carries a badge: the percentage done while it runs, "paused", or "stopped" if it failed.

## [0.4.0] - 2026-10-29

**Easier navigation and an optional street map, now stable.** This is [0.4.0-beta.1] with nothing added: tested on a real node and promoted.
See that section below for the details.

- The sidebar is grouped (Overview, Mesh, Insights, Answers, Radio), **Go to…** (`Ctrl+K`, `⌘K` or `/`) finds a page or a node, the phone gets a
  menu button, and Model, Radio and Knowledge are in tabs.
- **Street map**, optional and off by default: *Settings → Mesh and radio → Street map*, or the switch on the Map page. This server fetches
  OpenStreetMap pictures only for what you look at, keeps them, and the browser never contacts OpenStreetMap itself. OpenStreetMap can see this
  computer's address and which places were asked for; nothing about nodes is sent.

The compose file is unchanged: update the image from Settings → Updates (or let the node do it).

## [0.4.0-beta.1] - 2026-10-29

A test version for the beta channel: easier navigation, and an optional street map.

### Added
- **Street map (optional, off by default).** *Settings → Mesh and radio → Street map*, or the new *Street map* switch on the Map page (which
  explains first and asks), draws OpenStreetMap roads and towns behind the nodes, on the Map and on a traceroute. **This server**, never your
  browser, downloads each small picture once and keeps it in the data folder (up to 500 MB, refreshed after a month, *Delete saved pictures*
  empties it), so places you have looked at keep working offline. It fetches only what you are looking at (never in bulk), at most two at a
  time, with a User-Agent naming LoRaPedia, as OpenStreetMap's tile policy asks; the address is fixed (not a setting), a reply that is not a
  picture is thrown away, and without internet the map falls back to the plain grid with a notice. OpenStreetMap can see this computer's
  address and which places were asked for; nothing about nodes is sent. The credit "© OpenStreetMap contributors" is shown on the map. In the
  dark theme the pictures are darkened to fit.
- **Go to…** (`Ctrl+K`, `⌘K` or `/`, or the button above the sidebar / the magnifier on a phone): type a few letters to jump to a page, or to a
  node by its name or id.
- A **Mesh** card on the Dashboard (how many nodes, how many heard in the last hour).

### Changed
- **The sidebar is grouped** instead of one list of sixteen: Overview (Dashboard, Live Feed), Mesh (Nodes, Map, Messages, Traceroute), Insights
  (Link quality, Telemetry, Trends), Answers (Knowledge, Model, Limits), Radio (Connection, Radio settings), and Settings and the Setup guide
  at the bottom. It stays in view while a long page scrolls, every page has its own icon, each page shows its group above the title and the
  browser tab names the page.
- **On a phone** the menu is a button that opens the same grouped list, and the top bar fits on one line.
- **Long pages are in tabs**: Model (Models, Tuning and prompt, Try it), Radio (Connection, Replies) and Knowledge (Library, Indexing, Test a
  search), like Settings already was.
- Names agree everywhere: the status dots at the top say Radio, Model and Knowledge (they said LLM and Index), the Radio page is titled
  *Radio connection*, the Knowledge page *Knowledge*. The help text that pointed at the Radio page for blocking and allowing now points at Limits.

## [0.3.0] - 2026-10-28

**The MeshtasticLLM merge is finished and is now the stable release.** LoRaPedia remembers the mesh around it, answers questions about
it, and lets the operator talk to the mesh and tune the radio, while still answering questions by direct message only. Everything below
was tested as betas ([0.2.0-beta.1] to [0.3.0-beta.1]); each item has its details in those sections.

### Added
- **Mesh data and a Nodes page**: every node the radio hears (name, hardware, role, hops, signal, battery, sensors, position), kept in a
  separate `mesh.db` for 30 days by default (Settings → Mesh data; off if you like). Message content is never kept.
- **Map, Telemetry and Trends**: an offline map (no pictures fetched from the internet), sensor averages and history with a CSV export,
  and when the mesh is busiest. Temperature and distance units are settings.
- **Traceroute and Link quality**: on your click only, to one node at a time; signal against distance for directly heard nodes.
- **Mesh AI**: "how many nodes are around?", "which node has the lowest battery?" and the like are answered from what the radio has heard,
  by a plain-rule router with read-only lookups and **no language model**. Per-person follow-up memory (in RAM only, `?reset` clears it).
- **Messages and delivery tracking**: send a direct message to a node from the app, see for every answer whether it was delivered,
  passed on, not confirmed or failed, and a 30-minute chat window in which the AI stays quiet. A public post exists but is **off by
  default** and is a separate path the AI can never use.
- **Radio settings page**: read the radio's own settings; **changing them is off until you turn it on**, every change is checked,
  confirmed and preceded by a saved copy; set the radio's name and a fixed position. Passwords and keys are never shown or written.
- **Limits page**: a daily question limit per node, with a limit of its own for any node, plus block and allow lists.
- Long lists are paged, big pages are in tabs, and picking a node is a search box.

### Changed
- Updates read up to 100 releases and choose the highest version numerically (GitHub lists `beta.10` below `beta.2`). **To get the fixed
  updater, run `docker compose pull updater && docker compose up -d updater` once.**
- Block and allow lists moved from the Radio page to the Limits page.

### Fixed
- CSV exports can no longer run a stranger's text as a spreadsheet formula.
- A long answer from the model's own memory is continued over several messages instead of being cut off with "...".

### Upgrading from 0.1.x
The compose file is unchanged since 0.1.11: update the image from Settings → Updates (or let the node do it). Your settings, question
log and index are kept; a new `mesh.db` appears beside them. Nothing new is switched on that transmits: radio settings editing and
public posting are off, and traceroute and messages go out only when you ask.

## [0.3.0-beta.1] - 2026-10-28

A test version for the beta channel. **It replaces 0.2.0-beta.10, which no node could see.** Everything in beta.10 (per-node limits, the Limits
page, the end of the MeshtasticLLM merge) is here.

### Fixed
- **A beta numbered 10 or more was invisible to every update check.** GitHub lists releases in an order that compares `beta.10` as text, so
  `v0.2.0-beta.10` came out below `beta.2`, and the app and the updater each read only the newest five releases. They now read up to 100
  and choose the highest themselves, numerically. **Nodes running 0.2.0-beta.9 or older cannot see beta.10, and still read five**: that is
  why this release is numbered 0.3.0-beta.1 (it lists first). To get the fixed updater, run `docker compose pull updater && docker compose up
  -d updater` once. docs/RELEASING.md now says how to number betas.
- Tests only: every test that connects a radio now unsubscribes it when it ends (a leftover caused a flaky "Dead Listener called" on Python
  3.11 and 3.12).

## [0.2.0-beta.10] - 2026-10-28

A test version for the beta channel: the MeshtasticLLM merge, phase 7. **This completes the merge.**

### Added (beta: the MeshtasticLLM merge, phase 7: per-node limits)
- **Limits page.** A **daily limit** of questions per node (Questions per node per day; 0 = none), with a number of its own for any node.
  A node that reaches its limit is told once ("Daily limit reached (N questions). Try again tomorrow.") and ignored until midnight. Commands
  never count; a question counts when it is accepted (even one held for its turn or one whose answer fails); one turned away as "busy" does
  not; the count survives a restart (it is read back from the question log). The page lists everyone the radio has heard or who has asked,
  with today's questions against the limit, in tabs (everyone, asked today, blocked, allowed, own limit), a search box and pages.
  Block / allow moved here from the Radio page, and a node you have not heard can be added by its id.

### Fixed
- Tests only: the checks that radios are closed after each test now track the exact radios a test creates (the earlier version failed on
  Python 3.11).

## [0.2.0-beta.9] - 2026-10-28

A test version for the beta channel: the MeshtasticLLM merge, phase 6, and easier-to-use lists.

### Added (beta: the MeshtasticLLM merge, phase 6: Radio settings)
- **Radio settings page.** Look at what is stored on the radio itself (region, role, LoRa preset, hop limit, power, position, telemetry
  intervals and more) in tabs, with a search box to find a setting. **Changing is off until you turn it on** (Settings → Mesh and radio →
  Radio settings editor). Then every change is type- and range-checked against the radio's own definitions, shown with what could go wrong,
  confirmed, written in one transaction, and preceded by a saved copy you can restore (the newest 40 are kept; restoring a copy from another
  radio needs "Restore anyway"). Also the radio's name, and a **fixed position** for a radio with no GPS (it is shared with the mesh, and it
  lets the Map place the radio and link distances be worked out). Wi-Fi passwords, security keys, MQTT credentials and the Bluetooth PIN are
  never shown, copied or written. Not yet tried against a real radio's firmware.

### Changed
- **Long lists are paged, big pages are tabbed, and choosing a node is a search.** The Nodes, Live Feed, Telemetry readings, Traceroute
  history, Link quality, Radio node and Messages lists show a page at a time; Settings and Radio settings are split into tabs; and the
  node drop-downs (Traceroute, Messages) are replaced by a box where you type part of a name or id. Long choices on the Radio settings page
  (such as the region) are searchable too.

## [0.2.0-beta.8] - 2026-10-28

A test version for the beta channel: the MeshtasticLLM merge, phase 5.

### Added (beta: the MeshtasticLLM merge, phase 5: Messages and delivery tracking)
- **Delivery tracking.** Every message sent over a real radio asks for an acknowledgement and the Live Feed shows what came back for each
  answer: Delivered (the other node confirmed it), Passed on (a neighbour repeated it), Sent, waiting, Not confirmed (nothing heard in 3
  minutes) or Failed with the reason the mesh gave. An answer in several parts is delivered only when every part was. Not yet checked
  against a real radio: matching the acknowledgements is tested with the library's packet shapes.
- **Messages page.** Write to a node yourself: a direct message to one node (never to everyone, never to this radio), with the delivery
  report. After you write to a node, what it sends back (unless it starts with `?` or `!wiki`) is kept on the page and the AI does not
  answer it, for 30 minutes (Settings → Messages; 0 = off; "End chat" closes it at once). Your messages are kept in `lorapedia.db` for the
  mesh-data retention period.
- **Public posts, off by default.** Settings → Messages → "Allow me to post to a public channel". When on, you can type a message, choose
  a channel, confirm, and post (once a minute, real radio only). The AI still can never post publicly: the post is a separate method that
  only the Messages page calls, tests read the source to check it, and the rule "LoRaPedia replies by direct message only" is unchanged.

### Fixed
- **A long answer from the AI's own memory was cut off with "..." at 200 bytes and never continued.** Guesses were limited to one message
  on purpose; they now continue over several messages like any answer, and one that comes out too long is rewritten once to fit instead of
  being cut mid-sentence. The warning stays at the start of the first part.

## [0.2.0-beta.7] - 2026-10-28

A test version for the beta channel: the MeshtasticLLM merge, phase 4.

### Added (beta: the MeshtasticLLM merge, phase 4: Mesh AI)
- **Questions about the mesh.** "How many nodes are around?", "which node has the lowest battery?", "nearest router", "who was last heard?",
  "which nodes have gone quiet?", "temperature outside", "best links", "when is the mesh busiest?", "what's been happening on the mesh?",
  "tell me about node …", "battery history for …", "what time is it?" are answered from what the radio has heard. A plain-rule router
  picks one of a fixed menu of hand-written, read-only lookups; **no language model is involved**, so a node name chosen by a stranger can
  never act as an instruction, nothing can transmit or change anything, and what is not known is said so instead of guessed. The router
  needs mesh wording or a node that really exists ("what is the temperature of the sun" still goes to Wikipedia; checked against 1,400
  generic questions). Replies use a node's own name, never the private label or note you gave it, with control and invisible characters
  removed. Settings → Mesh AI turns it off.
- **Follow-up questions.** Each person's last three answered questions are remembered (in memory only: nothing on disk, nothing in a
  backup, nothing shared between people) so "and its population?" works after "what is the capital of Japan?". Forgotten after 24 hours
  idle (adjustable, 0 = off), on `?reset` / `?forget`, or at restart. Mesh answers and guesses from the model's memory are never remembered.
  Questions that depend on the date are given today's date.
- Live Feed marks mesh answers; the Model page's try-it box answers mesh questions too; Settings → Mesh AI shows how many people are
  being remembered and can forget them all.

## [0.2.0-beta.6] - 2026-10-28

A test version for the beta channel: the MeshtasticLLM merge, phase 3.

### Added (beta: the MeshtasticLLM merge, phase 3)
- **Traceroute.** Pick a node and LoRaPedia asks the mesh which way a packet takes to it and back, with the signal at each hop, shown as
  two lists and on a map (solid there, dashed back, dotted across hops with no known position) and saved to a history. A traceroute
  transmits and every node on the path repeats the packet, so it is deliberately hard to do by accident: only on your click (never on a
  schedule, never from the AI), to one node (never to everyone, never to this radio, only a node already heard), one at a time, the same
  node at most once every 30 seconds, with a 60 second wait. Without a radio it makes up a route from known nodes and says so. The radio
  adapter has no way to send a traceroute to everyone.
- **Link quality.** For the nodes your radio hears directly (no relay): signal against distance with the weakest decodable signal marked,
  best and weakest links, the farthest one, and a table with average, worst and best signal, RSSI, packets, distance and last heard. The
  Map can overlay the same links, coloured by signal and thicker for nodes heard more often. Distances need this radio to have a known
  position.

## [0.2.0-beta.5] - 2026-10-28

A test version for the beta channel: the MeshtasticLLM merge, phase 2.

### Added (beta: the MeshtasticLLM merge, phase 2)
- **Map.** Every node with a known position drawn on a plain offline grid (Web Mercator) with latitude and longitude lines and a scale
  bar: drag to move, scroll or use the buttons to zoom, arrow keys to pan, search, filter by how recently a node was heard, optional
  movement trails, and a card with the node's details and its distance from this radio. No background picture, so nothing is fetched
  from the internet.
- **Telemetry.** Sensor averages around you (each node counted once however often it reports, impossible values already dropped), a
  history chart of battery, voltage, channel use, temperature, humidity, pressure or uptime for the nodes you choose, a table of the
  newest readings and a CSV export.
- **Trends.** When the mesh is busiest by hour of the day, a heat map of the last week, and how many relays packets passed through.
  Counts only. They say plainly when too little has been recorded to mean much yet.
- **Temperature (°C/°F) and distance (km/mi) settings**, used by the new pages and the Nodes page.

### Fixed
- **CSV exports could run a stranger's text as a spreadsheet formula.** Names and questions come from strangers, and a cell that starts
  with `=`, `+`, `-` or `@` is run by spreadsheet programs. The question-log export and the new telemetry export now mark such cells as
  plain text.
- A position copied from the radio's node list is now dated by when it was fixed, not by when LoRaPedia copied it.

## [0.2.0-beta.4] - 2026-10-28

A test version for the beta channel. It adds nothing of its own beyond [0.2.0-beta.3] and brings in [0.1.10] and [0.1.11] (below):
the Radio page explains why a USB radio will not connect, and **Return to stable** (Settings → Updates) gives a beta the way back to
the newest stable release. Needs the new updater container to use it: `docker compose pull updater && docker compose up -d updater`.

## [0.2.0-beta.3] - 2026-10-28

A test version for the beta channel. **It replaces 0.2.0-beta.2, which could not start with its own compose file** (see [0.1.9] below:
the compose file crash-looped the app). Nothing else is new; everything in beta.1 and beta.2 is here.

## [0.2.0-beta.2] - 2026-10-28

A test version for the beta channel. It adds nothing of its own beyond [0.2.0-beta.1] and brings in everything released as [0.1.8]
(below): a USB radio is found automatically in Docker, and LoRaPedia answers direct messages only, by construction.

## [0.2.0-beta.1] - 2026-10-28

A test version for the beta channel (Settings → Updates → Release channel → Beta). Stable installs never receive it.

### Added (beta: the MeshtasticLLM merge, phase 1)
- **The mesh database and a Nodes page.** The radio adapter now listens to every packet (not only text) and LoRaPedia remembers each
  node it hears in a separate `mesh.db`: name, hardware, role, hops (measured, not just reported), signal, battery and sensor
  readings, last position, plus per-hour packet, signal and hop counts and a position trail. Message content is never kept. The
  **Nodes** page lists them with search and filters, shows each node's history, and lets you add your own label and note.
  **Settings → Mesh data** turns recording off and sets how long history is kept (30 days by default). A made-up mesh can be loaded to
  try the page without a radio. Not yet verified on a real radio: parsing is tested against the library's packet shapes with fakes.

## [0.1.11] - 2026-10-28

### Added
- **Return to stable** (Settings → Updates, while you run a beta). Updates never go backwards, so a node that tried the beta channel
  could not get back to the stable release without editing files by hand. The button sets the channel to Stable, takes a backup of
  the settings and question log, and installs the newest stable release, signature-verified like any release. It is the only downgrade
  there is: the updater accepts it only from a node running `X.Y.Z-beta.N`, only to the newest stable release, and only for the exact
  request `{"channel": "stable", "leave_beta": true}`; a stable node is still never moved to an older release. Installs that cannot do
  it themselves are shown the commands, and an updater container too old to know about it is named as such. Needs the new updater
  image (`docker compose pull updater && docker compose up -d updater`).

## [0.1.10] - 2026-10-28

### Added
- **On Linux, the Radio page says why a USB radio will not connect, and what to type.** It looks at what is really there (the device
  files, which group owns them, their permissions, which groups this program is in) and tells the usual causes apart: no data cable or
  no device at all; not being in the device's group (with the right `usermod` command for the group this computer actually uses); a
  device file only root can open, or one its group cannot write to (with a ready-made udev rule); another program holding the port
  (ModemManager, the Meshtastic app or CLI); in Docker, a container that learned the device's group before the radio was plugged in
  (restart it) or that was started from an old compose file; and a radio that is reachable but does not answer. Commands can be copied.
  Nothing is shown on other systems or when you use a network or Bluetooth radio.

## [0.1.9] - 2026-10-28

### Fixed
- **The `docker-compose.yml` of 0.1.8 crash-looped the app.** It named an entrypoint to protect against an old image, and in Compose
  setting `entrypoint` also clears the image's own command, so the entrypoint ran with nothing to run (`setpriv: No program
  specified`) and the container restarted for ever. Nothing caught it because CI started the image with `docker run`, not through the
  compose file. The compose file now repeats the command, a lint rule and a test keep it equal to the Dockerfile's, and CI starts the
  app from `docker-compose.yml` itself (`scripts/compose_smoke.sh`): it must become healthy, run as uid 10001 and hold no
  capabilities. **If you fetched the compose file of 0.1.8, fetch this one.** Installs that kept an older compose file were not
  affected: the image alone still runs as before.

## [0.1.8] - 2026-10-28

### Changed
- **LoRaPedia can no longer reply in a public channel, by construction.** Direct messages only was already the default; it is now the
  only behaviour, not a setting. The "In a channel" and "Both" trigger modes and the channel selector are gone from the Radio page, and
  an older settings file that asks for them is ignored. The rule is enforced in four places, each with its own test that fails if that
  place is weakened: the message parser (a public message is never a question or command), the engine (it refuses to send for a public
  packet), the radio manager (a message with no addressee is refused) and the Meshtastic adapter (it has no way to broadcast).

### Changed
- **A USB radio is found automatically in Docker, whatever it is.** No device path or group number to look up, nothing to put in an
  override file: plug any Meshtastic radio into USB, before or after starting, and it is found again after a reset or a replug
  (even when it returns under another number). The compose file mounts the host's `/dev` read-only at `/hostdev`, allows only USB
  serial ports to be opened, and starts the container as root with just SETUID and SETGID; `docker/entrypoint.sh` reads the groups of
  the serial devices, becomes the unprivileged user (uid 10001) with those groups, drops every capability, and only then runs the
  app. The app never runs as root. A radio plugged in later whose group was not seen at start needs `docker compose restart
  lorapedia` once. **Existing installs get this by fetching the new `docker-compose.yml`** (the image alone, with an older compose
  file, runs as before). Update the image first (Settings → Updates), then use the new compose file: this compose file names the entrypoint, so an older image fails to start instead of silently running as root. Untested: rootless Docker and Podman.

## [0.1.7] - 2026-10-28

### Fixed
- **Updating to a beta with an old updater container looked like nothing happened.** An updater from before the beta channel ignores
  the request and answers "Already on the newest release". The Updates card now says the updater is too old and shows the two
  commands that fix it (`docker compose pull updater && docker compose up -d updater`).

## [0.1.6] - 2026-10-28

### Added
- **A beta update channel** (Settings → Updates → Release channel). Beta nodes also receive test versions (`vX.Y.Z-beta.N`) built
  from the new `beta` branch, signed and verified exactly like releases; stable nodes never see them. Only that exact tag form is
  accepted, a beta sorts below the release it leads to, and leaving the channel never downgrades. The release workflow now lets a
  `-beta.N` tag be cut from `beta`; stable tags still must be on `main`. See docs/UPDATING.md and docs/RELEASING.md.

## [0.1.5] - 2026-10-28

### Added
- **Backups from the web interface** (Settings → Backups): back up now, a daily automatic backup (newest seven kept), download,
  add a backup file from another machine, and restore. A backup holds the settings and the question log (not Wikipedia or its
  index). Every file is verified before it is kept or restored, and a restore first saves what it replaces, so it can be undone.
  The backups the updater takes before an update appear in the same list.
- **`?ping`**: a range test. It replies with how the sender's message reached the node (direct, or the number of relays and the last
  hop's signal): SNR and RSSI. It needs no model and no library.

### Changed
- **No `[wp:Title]` source tag in replies over the radio.** It cost airtime for something only the person running the node needs.
  The Live Feed and the "try it" box still show the sources, and the one that best supports the answer is now listed first (the old
  tag took the top search hit, which for "capital of France" was Île-de-France, not Paris). The "Add a source tag" setting is gone.

## [0.1.4] - 2026-10-28

### Changed
- The README states that the project was generated by AI under the direction of a human.

### Fixed
- **"What is the capital of Japan?" found no answer in Wikipedia.** The four passages sent to the model were Nagoya, Hiroshima,
  Nagasaki and Sendai (each "the capital of" a prefecture in Japan), so the node fell back to the model's own memory. Retrieval now
  looks 60 deep per signal instead of 20 and boosts the article named by the subject of an "attribute of subject" question. On 94
  such questions against the real 49,864-article index, the answer reaches the model for 92 (was 89), and the capital of Japan
  now comes from the library. Search time is unchanged.

## [0.1.3] - 2026-10-28

### Added
- Moving a `./run.sh` install into Docker: `scripts/migrate-to-docker.sh` copies the data folder into the compose volume (the
  source is never changed) and fixes what points at the old machine (the library's absolute file paths and a saved `localhost`
  Ollama address). See `docs/MIGRATING.md`.
- `docker-compose.override.example.yml`: machine-specific settings (USB radio, GPU, port) go in `docker-compose.override.yml`, which
  git ignores.

### Changed
- The compose file sets `OLLAMA_CONTEXT_LENGTH=4096` (the 3B model then needs about 2.4 GB of GPU memory instead of about 4 GB, so
  both models fit a 4 GB card) and keeps the GPU kernel cache in the models volume (the first answer after the Ollama container is
  recreated took about 2 minutes; with the cache it takes seconds). Existing installs get this by fetching the new compose file.

### Fixed
- **The updater ignored `docker-compose.override.yml`.** It names the compose file explicitly, which stops Compose from loading the
  override, so an automatic update would have silently dropped a USB radio mapping or GPU setting kept there. It now includes the
  override (for the switch and for the roll-back).

## [0.1.2] - 2026-10-26

### Fixed
- **Automatic updates stopped at signature verification.** cosign must cache Sigstore's trust root, and the updater's root
  filesystem is read-only, so it failed with "read-only file system" before it could verify anything. Its cache now lives in the
  updater's state volume (`TUF_ROOT=/state/tuf`) and `HOME=/tmp`. The updater failed *safe*: nothing was installed and the app
  kept running. Found by performing a real 0.1.0 to 0.1.1 update. CI now runs cosign under the same read-only flags.

### Upgrading from 0.1.1
The updater image changed. Once, run `docker compose pull updater && docker compose up -d updater`. From then on updates are automatic.

## [0.1.1] - 2026-10-26

### Fixed
- **Automatic updates failed at the switch step on a real Docker install.** The updater runs as root with every capability dropped,
  so it could not write the new image into `.env` in a folder owned by you. It now has exactly two capabilities back
  (`DAC_OVERRIDE` to write there, `CHOWN` to hand the file back to you), and the new `.env` keeps its owner and permissions.
  Found by running the 0.1.0 images against a real Docker daemon.
- A leftover `.env.tmp` from an interrupted update could block every later update. It is now replaced, and removed on failure.

### Upgrading from 0.1.0
The compose file is yours, so an update cannot change it. Once, fetch the new `docker-compose.yml` (`git pull`, or download it from
this release) and run `docker compose pull updater && docker compose up -d updater`. From then on updates are automatic.

## [0.1.0] - 2026-10-26

First release.

### Added
- Answers questions sent over a Meshtastic mesh as direct messages, from a local offline copy of Wikipedia, using a local
  language model (Ollama). Replies are fitted to the message size limit by UTF-8 **bytes** (default 200), optionally as
  numbered parts `(1/3)` up to an admin-set maximum.
- Retrieval: Kiwix ZIM reader, resumable two-pass indexing (every article's opening first, then the rest), hybrid
  keyword + vector search with rank fusion and reranking.
- One-click Wikipedia packages (Quick Reference, Popular Articles, Complete) that download, add to the library and index.
- Questions that arrive inside a node's cooldown are queued and answered in order instead of rejected.
- When the library has nothing, answers from the model's own memory, always prefixed `[AI memory - may be wrong]`.
- Real radio over USB, network or Bluetooth, with a built-in simulator and automatic fallback.
- Web interface: setup wizard, dashboard, live feed, knowledge base, model, radio and settings pages; light and dark themes.
- Docker image and Compose file (with Ollama), and a `run.sh` script for running without Docker.
- Update checks against this repository's GitHub releases, with automatic, verified, roll-back-on-failure updates for Docker
  installs; Wikipedia packages keep themselves up to date when the computer is online.
