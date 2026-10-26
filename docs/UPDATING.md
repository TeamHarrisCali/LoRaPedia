# Updating LoRaPedia

## What updates, and when

| Thing | Checked | Applied |
|---|---|---|
| LoRaPedia (Docker + updater) | daily, from this repo's GitHub Releases | automatically, when the node is quiet (switchable) |
| LoRaPedia (Docker, no updater / source checkout) | daily | by hand (commands shown in Settings → Updates) |
| Wikipedia editions | daily, from Kiwix | automatically, when quiet and the disk has room (switchable) |

Everything is under **Settings → Updates**. "Check now" works even with the daily checks off.

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
   the `.env` next to `docker-compose.yml`, and runs `docker compose up -d --no-deps lorapedia`.
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

Your settings backup from before the update is in the data volume under `backups/` if you ever need it (restore by replacing
`lorapedia.db` while the app is stopped).

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
