# Changelog

All notable changes to LoRaPedia. The format follows [Keep a Changelog](https://keepachangelog.com/), and versions follow
[Semantic Versioning](https://semver.org/). While the version is 0.x, anything may change between releases.

## [Unreleased]

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
