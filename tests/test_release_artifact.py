"""Exercise the publication boundary through its actual command-line interface."""

import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / ".github/scripts/release_artifact.py"
REPOSITORY = "arcade-agent/arcade-agent"
SHA = "a" * 40


def _build_fixture(root: Path, wheel_version: str = "0.4.0", name: str = "arcade-agent") -> Path:
    dist = root / "dist"
    dist.mkdir()
    with zipfile.ZipFile(dist / "arcade_agent-0.4.0-py3-none-any.whl", "w") as wheel:
        wheel.writestr("arcade_agent-0.4.0.dist-info/METADATA",
                      f"Metadata-Version: 2.1\nName: {name}\nVersion: {wheel_version}\n")
    data = b"Metadata-Version: 2.1\nName: arcade-agent\nVersion: 0.4.0\n"
    with tarfile.open(dist / "arcade_agent-0.4.0.tar.gz", "w:gz") as sdist:
        info = tarfile.TarInfo("arcade_agent-0.4.0/PKG-INFO")
        info.size = len(data)
        sdist.addfile(info, io.BytesIO(data))
    (root / "pyproject.toml").write_text('[project]\nversion = "0.4.0"\n')
    return dist


def _run(command: str, dist: Path, output: Path | None = None) -> subprocess.CompletedProcess[str]:
    args = [sys.executable, str(SCRIPT), command, "--dist", str(dist),
            "--repository", REPOSITORY, "--sha", SHA]
    if command == "create":
        args.extend(["--pyproject", str(dist.parent / "pyproject.toml")])
    env = {key: value for key, value in os.environ.items() if key != "GITHUB_OUTPUT"}
    if output:
        env["GITHUB_OUTPUT"] = str(output)
    return subprocess.run(args, capture_output=True, text=True, env=env, timeout=10)


def test_round_trip_records_both_distribution_hashes_and_exports_version(tmp_path: Path) -> None:
    dist = _build_fixture(tmp_path)
    created = _run("create", dist)
    assert created.returncode == 0, created.stderr
    manifest = json.loads((dist / "release-build.json").read_text())
    assert manifest["sha"] == SHA
    assert manifest["repository"] == REPOSITORY
    assert len(manifest["files"]) == 2
    for name, digest in manifest["files"].items():
        assert hashlib.sha256((dist / name).read_bytes()).hexdigest() == digest
    output = tmp_path / "outputs"
    verified = _run("verify", dist, output)
    assert verified.returncode == 0, verified.stderr
    assert output.read_text() == "version=0.4.0\n"


@pytest.mark.parametrize("field,value", [
    ("sha", "b" * 40),
    ("repository", "someone/arcade-agent"),
    ("version", "0.4.0\nready=true"),
    ("version", "0.4.1"),
    ("schema", True),
    ("schema", 2),
    ("files", []),
])
def test_rejects_mismatched_identity_and_invalid_schema(
    tmp_path: Path, field: str, value: object,
) -> None:
    dist = _build_fixture(tmp_path)
    assert _run("create", dist).returncode == 0
    manifest_file = dist / "release-build.json"
    manifest = json.loads(manifest_file.read_text())
    manifest[field] = value
    manifest_file.write_text(json.dumps(manifest))
    output = tmp_path / "outputs"
    assert _run("verify", dist, output).returncode != 0
    assert not output.exists()


def test_rejects_distribution_modified_after_ci_manifest_was_written(tmp_path: Path) -> None:
    dist = _build_fixture(tmp_path)
    assert _run("create", dist).returncode == 0
    with next(dist.glob("*.whl")).open("ab") as wheel:
        wheel.write(b"tampered after CI")
    result = _run("verify", dist)
    assert result.returncode != 0
    assert "hashes do not match" in result.stderr


@pytest.mark.parametrize("version,name", [("0.5.0", "arcade-agent"), ("0.4.0", "another")])
def test_rejects_mixed_version_or_wrong_package(tmp_path: Path, version: str, name: str) -> None:
    dist = _build_fixture(tmp_path, version, name)
    result = _run("create", dist)
    assert result.returncode != 0
    assert "name/version does not match" in result.stderr
    assert not (dist / "release-build.json").exists()


@pytest.mark.parametrize("mutation", ["extra", "missing", "symlink", "filename", "manifest-link"])
def test_rejects_extra_missing_linked_or_misnamed_files(tmp_path: Path, mutation: str) -> None:
    dist = _build_fixture(tmp_path)
    assert _run("create", dist).returncode == 0
    wheel = next(dist.glob("*.whl"))
    if mutation == "extra":
        (dist / "injected.py").write_text("raise RuntimeError('must not execute')")
    elif mutation == "missing":
        wheel.unlink()
    elif mutation == "symlink":
        outside = tmp_path / "outside.whl"
        wheel.rename(outside)
        wheel.symlink_to(outside)
    elif mutation == "filename":
        wheel.rename(dist / "arcade_agent-0.3.0-py3-none-any.whl")
    else:
        manifest = dist / "release-build.json"
        outside = tmp_path / "outside.json"
        manifest.rename(outside)
        manifest.symlink_to(outside)
    assert _run("verify", dist).returncode != 0


def test_rejects_path_traversal_in_manifest(tmp_path: Path) -> None:
    dist = _build_fixture(tmp_path)
    assert _run("create", dist).returncode == 0
    manifest_file = dist / "release-build.json"
    manifest = json.loads(manifest_file.read_text())
    manifest["files"]["../outside.whl"] = "a" * 64
    manifest_file.write_text(json.dumps(manifest))
    result = _run("verify", dist)
    assert result.returncode != 0
    assert "Invalid distribution filename/hash" in result.stderr


@pytest.mark.parametrize("state,ready", [
    ("complete", False), ("wheel-missing", True), ("sdist-missing", True), ("empty", True),
    ("different-bytes", None), ("invalid-json", None), ("duplicate", None),
])
def test_pypi_preflight_handles_partial_uploads_and_immutable_file_conflicts(
    tmp_path: Path, state: str, ready: bool | None,
) -> None:
    dist = _build_fixture(tmp_path)
    assert _run("create", dist).returncode == 0
    files = json.loads((dist / "release-build.json").read_text())["files"]
    urls = [{"filename": name, "digests": {"sha256": digest}} for name, digest in files.items()]
    if state == "wheel-missing":
        urls = [url for url in urls if not url["filename"].endswith(".whl")]
    elif state == "sdist-missing":
        urls = [url for url in urls if not url["filename"].endswith(".tar.gz")]
    elif state == "empty":
        urls = []
    elif state == "different-bytes":
        urls[0]["digests"]["sha256"] = "b" * 64
    elif state == "duplicate":
        urls.append(urls[0])
    response = tmp_path / "pypi.json"
    response.write_text("not json" if state == "invalid-json" else json.dumps({"urls": urls}))
    output = tmp_path / "outputs"
    env = {**os.environ, "GITHUB_OUTPUT": str(output)}
    result = subprocess.run([
        sys.executable, str(SCRIPT), "pypi", "--dist", str(dist),
        "--repository", REPOSITORY, "--sha", SHA, "--response", str(response),
    ], env=env, text=True, capture_output=True, timeout=10)
    if ready is None:
        assert result.returncode != 0
        assert not output.exists()
    else:
        assert result.returncode == 0, result.stderr
        assert output.read_text() == f"version=0.4.0\nready={str(ready).lower()}\n"
