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
    # packaging reads the version from __init__.py (not installed metadata, which an editable install only refreshes on reinstall)
    assert '[tool.hatch.version]\npath = "lorapedia/__init__.py"' in read("backend", "pyproject.toml")


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
        allowed = {"updater": {"DAC_OVERRIDE", "CHOWN"}, "lorapedia": {"SETUID", "SETGID"}}.get(name, set())  # the only ones added back, only here
        if set(s.get("cap_add", [])) - allowed:
            problems.append(f"{name} adds capabilities it should not: {sorted(set(s['cap_add']) - allowed)}")
        if name in ("lorapedia", "updater") and not s.get("read_only"):
            problems.append(f"{name} must have a read-only root filesystem")
        socket = any("docker.sock" in str(v) for v in s.get("volumes", []))
        if socket and name != "updater":
            problems.append(f"{name} must not hold the Docker socket")
    app = svc["lorapedia"]
    if app.get("privileged"):
        problems.append("the app must not be privileged")
    if app.get("entrypoint") and not app.get("command"):
        problems.append("the app sets an entrypoint without a command: that clears the image's own CMD and the container crash-loops")
    if app.get("user") in ("0", "0:0", "root") and app.get("entrypoint") != ["lorapedia-entrypoint"]:
        problems.append("the app may start as root only through lorapedia-entrypoint, so an image without it fails instead of running as root")
    if app.get("user") in ("0", "0:0", "root") and set(app.get("cap_add", [])) != {"SETUID", "SETGID"}:
        problems.append("the app may start as root only to change identity (SETUID and SETGID, nothing else)")
    for rule in app.get("device_cgroup_rules", []):
        if rule not in ("c 166:* rmw", "c 188:* rmw"):
            problems.append(f"the app may open USB serial ports (166, 188) and no other device: {rule}")
    for v in app.get("volumes", []):
        if str(v).startswith("/dev") and str(v) != "/dev:/hostdev:ro":
            problems.append(f"the host's /dev may only be mounted read-only at /hostdev, never over the container's own: {v}")
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


def test_the_updater_only_writes_where_its_read_only_container_allows():
    """cosign caches Sigstore's trust root under HOME/TUF_ROOT, docker and compose under HOME. With a read-only root filesystem both
    must point at the tmpfs or the state volume, or verification fails closed before it can start (a real bug in v0.1.1)."""
    df = read("docker", "Dockerfile.updater")
    assert "HOME=/tmp" in df and "TUF_ROOT=/state/tuf" in df
    svc = yaml.safe_load(read("docker-compose.yml"))["services"]["updater"]
    assert svc["read_only"] is True and "/tmp" in svc["tmpfs"]
    assert any(str(v).endswith(":/state") for v in svc["volumes"])  # the volume TUF_ROOT lives in
    assert any(str(v).endswith(":/project") for v in svc["volumes"])  # where .env is written


def test_the_override_example_is_valid_inert_and_covers_the_machine_specific_settings():
    base = yaml.safe_load(read("docker-compose.yml"))["services"]
    ex = yaml.safe_load(read("docker-compose.override.example.yml"))["services"]
    assert set(ex) <= set(base)  # it can only configure services that exist
    assert "deploy" in ex["ollama"]  # the GPU
    assert "lorapedia" not in ex  # a USB radio is automatic: nothing to map by hand
    assert "docker-compose.override.yml" in read(".gitignore").splitlines()  # the real one is never committed
    assert "docker-compose.override.example.yml" not in read(".gitignore")  # but the example is shipped


def test_ollama_settings_that_were_measured_stay_in_the_compose_file():
    env = yaml.safe_load(read("docker-compose.yml"))["services"]["ollama"]["environment"]
    assert env["OLLAMA_CONTEXT_LENGTH"] == "4096"  # 3B model: ~2.4 GB instead of ~4 GB of GPU memory
    assert env["CUDA_CACHE_PATH"].startswith("/root/.ollama/")  # inside the persistent volume (127 s -> 5 s after a recreate)
    assert int(env["CUDA_CACHE_MAXSIZE"]) >= 1 << 30


# ---- which branch a tag may be cut from -------------------------------------------------------------------------------


def verify_branch_script():
    wf = yaml.safe_load(read(".github", "workflows", "release.yml"))
    step = next(s for s in wf["jobs"]["verify"]["steps"] if "merge-base" in s.get("run", ""))
    return step["run"]


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                        "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(cwd), "GIT_CONFIG_NOSYSTEM": "1"})


def run_verify(tmp_path, tag, commit_on, beta_exists=True):
    """Run the workflow's own branch check against a real repository: `commit_on` is where the tagged commit lives."""
    origin, work = tmp_path / "origin.git", tmp_path / "work"
    git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    git(tmp_path, "clone", str(origin), str(work))
    git(work, "switch", "-c", "main")
    (work / "a").write_text("1")
    git(work, "add", "a")
    git(work, "commit", "-m", "base")
    git(work, "push", "origin", "main")
    if beta_exists:
        git(work, "switch", "-c", "beta")
        (work / "b").write_text("2")
        git(work, "add", "b")
        git(work, "commit", "-m", "beta work")
        git(work, "push", "origin", "beta")
    if commit_on == "neither":
        git(work, "switch", "-c", "stray")
        (work / "c").write_text("3")
        git(work, "add", "c")
        git(work, "commit", "-m", "stray")
    elif commit_on == "main":
        git(work, "switch", "main")
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=work, check=True, capture_output=True, text=True).stdout.strip()
    git(work, "switch", "main")  # the workflow checks out the tag's commit; only the refs matter to the script
    out = subprocess.run(["bash", "-e", "-c", verify_branch_script()], cwd=work, capture_output=True, text=True,
                         env={"TAG": tag, "TAGGED_COMMIT": sha, "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(tmp_path),
                              "GIT_CONFIG_NOSYSTEM": "1"})
    return out.returncode


@pytest.mark.parametrize("tag,commit_on,passes", [
    ("v1.0.0", "main", True),
    ("v1.0.0-beta.2", "main", True),          # a beta of something already merged is fine
    ("v1.0.0-beta.2", "beta", True),          # the point: a beta may be cut from the beta branch
    ("v1.0.0", "beta", False),                # a stable release may only come from main
    ("v1.0.0-rc.1", "beta", False),           # and no other pre-release name opens the beta branch
    ("v1.0.0-beta.2", "neither", False),
    ("v1.0.0", "neither", False),
])
def test_the_workflow_only_lets_a_beta_tag_come_from_the_beta_branch(tmp_path, tag, commit_on, passes):
    assert (run_verify(tmp_path, tag, commit_on) == 0) is passes


def test_a_beta_tag_with_no_beta_branch_on_the_remote_is_refused(tmp_path):
    assert run_verify(tmp_path, "v1.0.0-beta.1", "neither", beta_exists=False) != 0


def test_the_tag_name_still_reaches_the_branch_check_only_through_an_environment_variable():
    wf = yaml.safe_load(read(".github", "workflows", "release.yml"))
    step = next(s for s in wf["jobs"]["verify"]["steps"] if "merge-base" in s.get("run", ""))
    assert "github.ref_name" not in step["run"] and "${{" not in step["run"] and step["env"]["TAG"] == "${{ github.ref_name }}"


def test_the_usb_radio_rules_each_fire_on_a_broken_copy():
    good = read("docker-compose.yml")
    assert any("privileged" in p for p in compose_problems(good.replace("    user: \"0:0\"", "    privileged: true\n    user: \"0:0\"", 1)))
    assert any("identity" in p for p in compose_problems(good.replace("cap_add: [SETUID, SETGID]", "cap_add: [SETUID, SETGID, SYS_ADMIN]", 1)))
    assert any("crash-loops" in p for p in compose_problems(good.replace('    command: ["lorapedia"]', "    #", 1)))  # the v0.1.8 bug
    assert any("fails instead of running as root" in p for p in compose_problems(good.replace('    entrypoint: ["lorapedia-entrypoint"]\n', "", 1)))
    assert any("USB serial" in p for p in compose_problems(good.replace('"c 188:* rmw"', '"a *:* rwm"', 1)))
    assert any("read-only at /hostdev" in p for p in compose_problems(good.replace("/dev:/hostdev:ro ", "/dev:/dev ", 1)))
    assert any("read-only at /hostdev" in p for p in compose_problems(good.replace("/dev:/hostdev:ro ", "/dev:/hostdev ", 1)))


def test_the_image_still_defaults_to_the_unprivileged_user_and_uses_the_entrypoint():
    """The compose file starts the container as root only so the entrypoint can pick up the radio's group; run without it (an older
    compose file, docker run) the image must still be the unprivileged user, and the entrypoint must still be what starts the app."""
    df = read("docker", "Dockerfile")
    assert "USER 10001:10001" in df and 'ENTRYPOINT ["lorapedia-entrypoint"]' in df and "docker/entrypoint.sh" in df
    assert (ROOT / "docker" / "entrypoint.sh").stat().st_mode & 0o111  # executable in the repository, so COPY keeps it so
    script = read("docker", "entrypoint.sh")
    assert "--no-new-privs" in script and "--bounding-set=-all" in script and 'APP_UID=10001' in script


def test_the_compose_command_is_what_the_image_would_have_run():
    """Setting `entrypoint` in compose replaces the image's CMD with nothing, so the command is repeated there. It must not drift
    from the Dockerfile's, or the compose file would start something other than the app."""
    cmd = re.search(r'^CMD (\[.*\])$', read("docker", "Dockerfile"), re.M).group(1)
    assert yaml.safe_load(read("docker-compose.yml"))["services"]["lorapedia"]["command"] == yaml.safe_load(cmd)


def test_the_ci_job_runs_the_compose_smoke_test():
    wf = read(".github", "workflows", "tests.yml")
    assert "scripts/compose_smoke.sh lorapedia-ci" in wf
    assert (ROOT / "scripts" / "compose_smoke.sh").stat().st_mode & 0o111
