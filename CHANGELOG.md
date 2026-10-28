# Changelog

All notable changes to LoRaPedia. The format follows [Keep a Changelog](https://keepachangelog.com/), and versions follow
[Semantic Versioning](https://semver.org/). While the version is 0.x, anything may change between releases.

## [Unreleased]

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
