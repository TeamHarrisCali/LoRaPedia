# Updating LoRaPedia

## What updates, and when

| Thing | Checked | Applied |
|---|---|---|
| LoRaPedia (Docker + updater) | daily, from this repo's GitHub Releases | automatically, when the node is quiet (switchable) |
| LoRaPedia (Docker, no updater / source checkout) | daily | by hand (commands shown in Settings → Updates) |
| Wikipedia editions | daily, from Kiwix | automatically, when quiet and the disk has room (switchable) |

Everything is under **Settings → Updates**. "Check now" works even with the daily checks off.

## The beta channel

By default a node only receives finished (**stable**) releases. In **Settings → Updates → Release channel** you can choose **Beta**,
which also receives test versions, tagged `vX.Y.Z-beta.N`, built from this project's `beta` branch before they are released. Use it to
try new features early. It is meant for a node you can afford to break.

- A beta is published exactly like a release (signed images, a GitHub *pre-release*) and is checked exactly like one: the updater
  accepts only a tag of the form `vX.Y.Z-beta.N` that GitHub flags as a pre-release, and only if its signature matches that exact tag.
  Any other pre-release name is ignored, and `latest` never points at a beta.
- A beta sorts **below** the release it leads to (`0.2.0-beta.3` < `0.2.0`), so a node on the beta channel is moved to the final
  `0.2.0` when it appears, and a stable node never sees any beta.
- A backup of your settings and question log is made before every update, and a version that does not start is rolled back. Your
  Wikipedia files and search index are **not** backed up, and a beta may add new data files; read its release notes.
- **Choosing Stable again does not downgrade by itself.** Updates never go backwards, so you stay on the beta you have until a stable
  release newer than it arrives. To go back now, use **Return to stable** (next point).
- **Return to stable.** While you run a beta, Settings → Updates shows a **Return to stable** button (Docker installs with the updater).
  It sets the channel to Stable, makes a backup of your settings and question log, and installs the newest stable release, verified like
  any release. This is the only downgrade there is: the updater accepts it only from a node running a beta, only to the newest stable
  release, and refuses it from any other version. Your Wikipedia library is kept; what the beta added (for example the Nodes data in
  `mesh.db`) is left in place but unused. For installs that cannot do it themselves the card shows the commands (`git switch main`
  and `git pull` for a source checkout; set `LORAPEDIA_IMAGE=ghcr.io/teamharriscali/lorapedia:latest` in `.env` for Docker without the
  updater). An updater container older than this feature says so and shows how to update it.
- Save the setting, then press **Check now**. For installs that cannot update themselves the page shows the matching commands
  (`git switch beta` for a source checkout; the image to put in `.env` for Docker without the updater).
- The request the app sends the updater carries one word, `beta` (or, to leave the beta, `stable` with `leave_beta: true`), and nothing else (no version, no image). The updater refuses any other
  field. A compromised app could therefore at worst move the node to a signed beta of this repository, or a node running a beta to the newest signed stable release, never to anything else.
- An older updater container simply ignores the channel and keeps offering stable releases, which is safe, but pressing Update now then
  appears to do nothing ("Already on the newest release"). The Updates card says so when it sees this. To get betas, pull the new one:
  `docker compose pull updater && docker compose up -d updater`, then press Update now again.

## What an update cannot change

An automatic update replaces the `lorapedia` container's image. It cannot change **your** `docker-compose.yml`, the **updater**
container itself, or the Ollama container: those are yours to update (`git pull`, or fetch `docker-compose.yml` from the release,
then `docker compose pull updater && docker compose up -d updater`). A release that needs this says so in its changelog under
"Upgrading".

## Update by hand

```bash
# Docker
docker compose pull && docker compose up -d
# Source checkout
git pull && ./run.sh
```

Your data (the `lorapedia-data` volume, or `./data`) is not touched by an update.

## Checking a release yourself

Every release image is signed with [cosign](https://docs.sigstore.dev/) using GitHub's identity for this repository's
release workflow, and has a build-provenance attestation. To check one:

```bash
# The signature: succeeds only for an image built by this repository's release workflow, from tag v0.2.0
cosign verify \
  --certificate-identity https://github.com/TeamHarrisCali/LoRaPedia/.github/workflows/release.yml@refs/tags/v0.2.0 \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/teamharriscali/lorapedia:0.2.0

# The provenance attestation (GitHub CLI)
gh attestation verify oci://ghcr.io/teamharriscali/lorapedia:0.2.0 --repo TeamHarrisCali/LoRaPedia
```

This is exactly what the updater runs before it installs anything.

## What the automatic update does, step by step

1. The app notices a newer stable release, waits until the node has been quiet for a few minutes, and makes a verified backup of
   its settings and log database in `data/backups/` (the newest five are kept).
2. It asks the updater to update. The request has **no parameters**.
3. The updater reads GitHub's latest stable release itself, refuses anything not strictly newer than what runs, pulls
   `ghcr.io/teamharriscali/lorapedia:<version>`, and verifies its signature (above). A failing image is deleted.
4. It keeps the current image as `lorapedia-rollback:previous`, records the verified image **by digest** as `LORAPEDIA_IMAGE` in
   the `.env` next to `docker-compose.yml`, and runs `docker compose up -d --no-deps lorapedia`, naming `docker-compose.yml` **and your `docker-compose.override.yml` if you have one**
   (so a USB radio mapping or GPU setting kept there survives the update).
5. It waits up to three minutes for the new container to be **healthy and to report the new version**. If not, it puts the
   previous image back and remembers the failed version so it is not retried.

The web page goes dark for about a minute while the container is replaced, then reconnects by itself.

## Going back

An automatic roll-back needs nothing from you. To go back by hand to the version you were on:

```bash
docker compose down lorapedia
printf 'LORAPEDIA_IMAGE=lorapedia-rollback:previous\n' >> .env     # or an older tag: ghcr.io/teamharriscali/lorapedia:0.1.0
docker compose up -d lorapedia
```

Your settings backup from before the update is in the data volume under `backups/`; restore it from *Settings → Backups*.

## Turning it off

- Stop *automatic* installs: Settings → Updates → untick **Install them automatically** (you still get notified).
- Stop checking: untick **Look for new LoRaPedia releases** (and the Wikipedia equivalents).
- Remove the updater entirely: delete the `updater:` service from `docker-compose.yml` (and run `docker compose up -d
  --remove-orphans`). You then update with the two commands above.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| "The updater could not be reached" | The updater container is not running: `docker compose ps`, `docker compose up -d updater`. |
| "The updater refused the request (bad token)" | The app and updater disagree about the shared token. `docker compose restart updater lorapedia`. |
| "The signature … could not be verified" | The image was not built by this repository's release workflow. Nothing was installed. If you run a fork, the identity in `updater/updater.py` must be changed to your repository. |
| "tried before and failed" | That version did not start correctly and was rolled back. A newer release will be tried. |
| "not a release" | You are running a locally built image (`VERSION=dev`), which is never auto-updated. |
| The image will not pull | GitHub packages published by a workflow can be private until made public once: Packages → the image → Package settings → Change visibility. |
