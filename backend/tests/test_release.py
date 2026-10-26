"""Release engineering: the version, the changelog, the tag check, and lint rules for the workflows and compose file.

Offline: nothing is built or published. Each lint rule is also proven to fire, on a deliberately broken copy.
"""
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("release_check", ROOT / "scripts" / "release_check.py")
RC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RC)

import lorapedia  # noqa: E402

VERSION = lorapedia.__version__
SHA = re.compile(r"^[0-9a-f]{40}$")


def read(*parts):
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


# ---- version & changelog ------------------------------------------------------------------------------------------


def test_the_version_is_valid_semver_and_has_one_source():
    assert RC.is_semver(VERSION) and RC.read_version() == VERSION
    assert 'version = "' not in read("backend", "pyproject.toml").split("[tool.hatch")[0].split("dynamic")[0]  # no second copy
    assert 'dynamic = ["version"]' in read("backend", "pyproject.toml")
    import importlib.metadata as md
    assert md.version("lorapedia") == VERSION  # packaging reads it from __init__.py


@pytest.mark.parametrize("bad", ["1", "1.2", "v1.2.3", "01.2.3", "1.2.3.4", "1.2.x", "", None, "1.2.3-"])
def test_invalid_versions_are_refused(bad):
    assert not RC.is_semver(bad)


@pytest.mark.parametrize("good", ["0.1.0", "1.0.0", "10.20.30", "1.0.0-rc.1", "1.0.0-alpha+001"])
def test_valid_versions_are_accepted(good):
    assert RC.is_semver(good)


def test_the_changelog_has_unreleased_and_a_section_for_this_version():
    log = read("CHANGELOG.md")
    assert re.search(r"^## \[Unreleased\]\s*$", log, re.M)
    assert RC.changelog_section(log, VERSION)


def test_changelog_section_parsing():
    log = "## [Unreleased]\n\n## [0.2.0] - 2026-01-02\n\n### Added\n- thing\n\n## [0.1.0] - 2026-01-01\n- old\n\n[0.2.0]: https://x\n"
    assert RC.changelog_section(log, "0.2.0") == "### Added\n- thing"
    assert RC.changelog_section(log, "0.1.0") == "- old"
    assert RC.changelog_section(log, "0.3.0") is None
    assert RC.changelog_section("## [0.2.0]\n\n## [0.1.0]\n- x", "0.2.0") is None  # an empty section does not count


def test_tag_check_accepts_only_v_plus_the_version(tmp_path):
    assert RC.check_tag("v" + VERSION) == []
    for bad in (VERSION, "v" + VERSION + "1", "refs/tags/v" + VERSION, "v9.9.9", ""):
        assert any("does not match" in p for p in RC.check_tag(bad))


def test_tag_check_reports_a_missing_changelog_section(tmp_path):
    (tmp_path / "backend" / "lorapedia").mkdir(parents=True)
    (tmp_path / "backend" / "lorapedia" / "__init__.py").write_text('__version__ = "0.5.0"\n')
    (tmp_path / "CHANGELOG.md").write_text("## [Unreleased]\n")
    assert any("no non-empty '## [0.5.0]'" in p for p in RC.check_tag("v0.5.0", root=str(tmp_path)))


def test_the_script_exit_codes_are_what_the_workflow_relies_on():
    run = lambda *a: subprocess.run([sys.executable, str(ROOT / "scripts" / "release_check.py"), *a], capture_output=True, text=True)
    assert run("check", "v" + VERSION).returncode == 0
    assert run("check", "v0.0.1").returncode == 1
    assert run("notes", VERSION).stdout.strip() == RC.changelog_section(read("CHANGELOG.md"), VERSION)
    assert run("notes", "9.9.9").returncode == 1
    assert run("version").stdout.strip() == VERSION
    assert run().returncode == 2


def test_the_updater_and_the_app_agree_on_what_a_release_tag_is():
    spec = importlib.util.spec_from_file_location("lp_updater", ROOT / "updater" / "updater.py")
    u = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(u)
    for tag in ("v1.2.3", "v0.1.0", "1.2.3", "v1.2", "v01.2.3", "v1.2.3-rc.1", "v1.2.3\n", "v١.2.3", "", "../v1.2.3"):
        assert u.valid_tag(tag) == bool(re.fullmatch(r"v(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})", tag, re.ASCII)), tag


# ---- lint rules for the workflows ---------------------------------------------------------------------------------


def workflow_problems(text: str) -> list[str]:
    """Rules that keep a workflow that signs what users auto-update to from being quietly weakened."""
    wf = yaml.safe_load(text)
    on = wf.get(True) or wf.get("on")  # YAML 1.1 reads a bare `on` as True
    problems = []
    if not isinstance(wf.get("permissions"), dict) or wf["permissions"] != {"contents": "read"}:
        problems.append("top-level permissions must be exactly contents: read")
    triggers = on if isinstance(on, dict) else {t: None for t in (on if isinstance(on, list) else [on])}
    for bad in ("pull_request_target", "workflow_run", "issue_comment"):
        if bad in triggers:
            problems.append(f"dangerous trigger {bad}")
    for name, job in wf["jobs"].items():
        for step in job.get("steps", []):
            uses = step.get("uses")
            if uses and not SHA.match(uses.split("@", 1)[-1]):
                problems.append(f"{name}: action not pinned to a commit SHA: {uses}")
            if "run" in step and "${{" in step["run"]:
                problems.append(f"{name}: an expression inside run: (shell injection): {step['run'][:60]!r}")
            if uses and uses.startswith("actions/checkout") and (step.get("with") or {}).get("persist-credentials") is not False:
                problems.append(f"{name}: checkout keeps credentials")
    return problems


def test_the_real_workflows_pass_every_rule():
    for f in ("tests.yml", "release.yml"):
        assert workflow_problems(read(".github", "workflows", f)) == [], f


def test_each_workflow_rule_fires_on_a_broken_copy():
    good = read(".github", "workflows", "release.yml")
    unpinned = re.sub(r"(actions/checkout)@[0-9a-f]{40}", r"\1@v7", good, count=1)
    assert any("not pinned" in p for p in workflow_problems(unpinned))
    assert any("permissions" in p for p in workflow_problems(good.replace("permissions:\n  contents: read\n\njobs:", "permissions: write-all\n\njobs:", 1)))
    assert any("expression inside run" in p for p in workflow_problems(good.replace('run: python3 scripts/release_check.py check "$TAG"', 'run: python3 scripts/release_check.py check ${{ github.ref_name }}')))
    assert any("checkout keeps" in p for p in workflow_problems(good.replace("          persist-credentials: false\n", "", 1)))
    assert any("dangerous trigger" in p for p in workflow_problems(good.replace("push:\n    tags:", "pull_request_target:\n    tags:", 1)))


def test_the_release_workflow_only_runs_on_version_tags_and_gives_each_job_only_what_it_needs():
    wf = yaml.safe_load(read(".github", "workflows", "release.yml"))
    assert (wf.get(True) or wf["on"]) == {"push": {"tags": ["v*.*.*"]}}
    jobs = wf["jobs"]
    assert jobs["verify"].get("permissions") is None and jobs["test"].get("permissions") is None  # read-only
    assert jobs["images"]["permissions"] == {"contents": "read", "packages": "write", "id-token": "write", "attestations": "write"}
    assert jobs["publish"]["permissions"] == {"contents": "write"}
    assert not any("checkout" in s.get("uses", "") for s in jobs["publish"]["steps"])  # runs none of our code
    assert not any("run" in s and "scripts/" in s["run"] for s in jobs["publish"]["steps"])
    assert set(jobs["images"]["needs"]) == {"verify", "test"} and set(jobs["publish"]["needs"]) == {"verify", "images"}
    steps = " ".join(str(s) for s in jobs["images"]["steps"])
    assert "cosign sign --yes" in steps and "@${DIGEST}" in steps  # signed by digest, never by a tag


def test_images_are_signed_with_the_identity_the_updater_verifies():
    spec = importlib.util.spec_from_file_location("lp_updater", ROOT / "updater" / "updater.py")
    u = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(u)
    # cosign records the workflow file and the tag ref; the updater requires exactly that, from exactly that issuer
    assert u.signing_identity("v1.2.3").endswith("/.github/workflows/release.yml@refs/tags/v1.2.3")
    assert u.OIDC_ISSUER == "https://token.actions.githubusercontent.com"
    assert u.IMAGE_REPO == "ghcr.io/teamharriscali/lorapedia" and u.REPO == RC.REPO
    assert f"ghcr.io/teamharriscali/${{{{ matrix.name }}}}" in read(".github", "workflows", "release.yml")


# ---- lint rules for compose and the Dockerfiles ---------------------------------------------------------------------


def compose_problems(text: str) -> list[str]:
    svc = yaml.safe_load(text)["services"]
    problems = []
    for name, s in svc.items():
        if name in ("lorapedia", "updater") and s.get("cap_drop") != ["ALL"]:
            problems.append(f"{name} must drop all capabilities")
        allowed = {"updater": {"DAC_OVERRIDE", "CHOWN"}}.get(name, set())  # the only capabilities added back, and only here
        if set(s.get("cap_add", [])) - allowed:
            problems.append(f"{name} adds capabilities it should not: {sorted(set(s['cap_add']) - allowed)}")
        if name in ("lorapedia", "updater") and not s.get("read_only"):
            problems.append(f"{name} must have a read-only root filesystem")
        socket = any("docker.sock" in str(v) for v in s.get("volumes", []))
        if socket and name != "updater":
            problems.append(f"{name} must not hold the Docker socket")
    if "docker.sock" not in str(svc["updater"].get("volumes")):
        problems.append("the updater needs the socket")
    if svc["updater"].get("ports"):
        problems.append("the updater must not publish a port")
    for p in svc["lorapedia"].get("ports", []):
        if not str(p).startswith("127.0.0.1:"):
            problems.append(f"the web interface (no password) must publish on loopback only: {p}")
    return problems


def test_the_real_compose_file_passes_every_rule():
    assert compose_problems(read("docker-compose.yml")) == []


def test_each_compose_rule_fires_on_a_broken_copy():
    good = read("docker-compose.yml")
    assert any("socket" in p for p in compose_problems(good.replace("      - updater-state:/run/lorapedia-updater:ro", "      - /var/run/docker.sock:/var/run/docker.sock\n      - updater-state:/run/lorapedia-updater:ro", 1)))
    assert any("loopback" in p for p in compose_problems(good.replace('"127.0.0.1:8000:8000"', '"8000:8000"')))
    assert any("publish a port" in p for p in compose_problems(good.replace("    # No `ports:`", '    ports: ["8081:8081"]\n    # No `ports:`', 1)))
    assert any("adds capabilities" in p for p in compose_problems(good.replace("cap_add: [DAC_OVERRIDE, CHOWN]", "cap_add: [SYS_ADMIN]")))
    assert any("read-only" in p for p in compose_problems(good.replace("    read_only: true            # the image's", "    # the image's", 1)))


def test_base_images_are_pinned_by_digest():
    for f in ("Dockerfile", "Dockerfile.updater"):
        froms = re.findall(r"^FROM\s+(?:--platform=\S+\s+)?(\S+)", read("docker", f), re.M)
        assert froms and all("@sha256:" in x for x in froms), (f, froms)


def test_the_app_image_runs_as_an_unprivileged_user_and_carries_its_version():
    df = read("docker", "Dockerfile")
    assert "USER 10001:10001" in df and "org.opencontainers.image.version" in df and "LORAPEDIA_INSTALL=docker" in df
    assert "HEALTHCHECK" in df and "org.opencontainers.image.source" in df
