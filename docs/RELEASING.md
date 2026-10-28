# Releasing

A release is a git tag such as `v0.2.0`. Pushing the tag makes GitHub Actions test the code, build and **sign** the container
images (amd64 and arm64), and publish a GitHub Release with notes from [CHANGELOG.md](../CHANGELOG.md). **Nothing is published until
a tag is pushed**, and only the maintainer should push one. Installs that have update checks on will see a stable release
within a day and (Docker + updater) apply it when idle.

## Cutting a release

1. **Pick the version** (semver; while 0.x anything may change). Edit `backend/lorapedia/__init__.py` (`__version__`, the only
   place) and move the `## [Unreleased]` items in `CHANGELOG.md` into a new `## [X.Y.Z] - YYYY-MM-DD` section.
2. `cd backend && pytest -q` and `python3 scripts/release_check.py check vX.Y.Z` should pass (CI runs both).
3. Merge to `main` through a normal pull request or push; CI must be green.
4. **Tag the commit at the tip of `main`** and push the tag:
   ```bash
   git switch main && git pull
   git tag -a vX.Y.Z -m "LoRaPedia X.Y.Z" && git push origin vX.Y.Z
   ```
5. Watch the **release** workflow (`gh run watch`). When it finishes, check it:
   ```bash
   cosign verify --certificate-identity https://github.com/TeamHarrisCali/LoRaPedia/.github/workflows/release.yml@refs/tags/vX.Y.Z \
     --certificate-oidc-issuer https://token.actions.githubusercontent.com ghcr.io/teamharriscali/lorapedia:X.Y.Z
   ```
6. **First release only:** GitHub may create the two packages (`lorapedia`, `lorapedia-updater`) as *private*. Make them public once
   (Packages → each → Package settings → Change visibility) or no install can pull them.

A version with a hyphen (`1.0.0-rc.1`) is published as a **pre-release**: it does not move `latest` and installs never update to it.

## Beta releases (the `beta` branch)

Work that is not ready for everyone lives on the `beta` branch, and is published as pre-releases that only nodes on the **beta
channel** receive (Settings → Updates). Stable releases still come only from `main`.

```bash
git switch beta                                   # do the work here; CI runs on every push to beta
# edit backend/lorapedia/__init__.py to X.Y.Z-beta.N and add a "## [X.Y.Z-beta.N]" section to CHANGELOG.md
python3 scripts/release_check.py check vX.Y.Z-beta.N
git tag -a vX.Y.Z-beta.N -m "LoRaPedia X.Y.Z-beta.N" && git push origin vX.Y.Z-beta.N
```

The workflow accepts a tag whose name contains `-beta.` from the tip of `beta` (or `main`); a stable tag must be on `main`, and any
other pre-release name is refused for the beta branch. The release is marked as a pre-release and `latest` does not move. The app
and updater accept exactly `vX.Y.Z-beta.N` on the beta channel and nothing else. To release the finished work, merge `beta` into
`main` (a pull request), set the plain version, and cut a normal release: nodes on the beta channel then move up to it, because a
final release sorts above its own betas. Keep `main` merged back into `beta` now and then so they do not drift apart.

## What the workflow does (`.github/workflows/release.yml`)

| Job | Does | Permissions |
|---|---|---|
| `verify` | tag is exactly `v` + `__version__`; CHANGELOG has a section for it; the tagged commit is on `main` (a `-beta.N` tag may instead be on `beta`); makes the notes | read |
| `test` | backend tests and the frontend build | read |
| `images` | builds and pushes both images (multi-arch), **signs each by digest with cosign (keyless)**, attests build provenance | packages, id-token, attestations (the only job) |
| `publish` | `gh release create` with the notes and `docker-compose.yml` | contents: write (the only job); **checks nothing out and runs none of our code** |

Every third-party action is pinned to a commit SHA, no secret is used except the run's own short-lived token, and the tag name
reaches a shell only through an environment variable. `tests/test_release.py` fails if any of that is weakened.

## What protects a release, and what does not

The workflow's checks stop a *mistaken* release (wrong number, forgotten changelog, an unmerged branch). They do **not** stop a
malicious person who can push to the repository: a workflow run from a tag uses the workflow file *at that tag*, so an edited
`release.yml` could skip every check, and Actions would still sign the result with the repository's identity. Because the
updater trusts exactly that identity, **who can create `v*` tags is the real control.** Do this once, before the first release:

- Settings → Rules → Rulesets → New **tag** ruleset, target `v*`, restrict creations, updates and deletions to the owner.
- Optionally a protected `release` environment with required reviewers, and immutable releases (Settings → General).
- Keep two-factor authentication on the owning account.

## If a release is bad

Fix it with a normal change and ship a **new version**; installs update to the newest. Do not edit or re-tag a published release.
If the images are dangerous, delete the release and the package version on GitHub; installs that already updated roll back only
if the new version fails its health check, so publish a fixed newer version quickly.
