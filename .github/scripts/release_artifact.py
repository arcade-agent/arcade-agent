"""Bind built distributions to their source commit and validate before publishing."""

import argparse
import hashlib
import json
import os
import re
import tarfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import Literal, TypedDict

MANIFEST = "release-build.json"
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
SHA = re.compile(r"[a-f0-9]{40}")
HASH = re.compile(r"[a-f0-9]{64}")


class BuildManifest(TypedDict):
    schema: Literal[1]
    repository: str
    sha: str
    version: str
    files: dict[str, str]


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Expected a string in release metadata")
    return value


def _identity(repository: str, sha: str, version: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Invalid repository")
    if not SHA.fullmatch(sha) or not VERSION.fullmatch(version):
        raise ValueError("Expected a commit SHA and stable major.minor.patch version")


def _distributions(dist: Path) -> list[Path]:
    files = sorted(file for file in dist.iterdir() if file.name != MANIFEST)
    if (
        len(files) != 2
        or sum(file.name.endswith(".whl") for file in files) != 1
        or sum(file.name.endswith(".tar.gz") for file in files) != 1
        or any(not file.is_file() or file.is_symlink() for file in files)
    ):
        raise ValueError("Expected exactly one wheel and one sdist, with no extra files")
    return files


def _package_metadata(file: Path) -> tuple[str, str]:
    if file.name.endswith(".whl"):
        with zipfile.ZipFile(file) as archive:
            entries = [item for item in archive.infolist() if item.filename.endswith(
                ".dist-info/METADATA"
            )]
            if len(entries) != 1 or entries[0].file_size > 1_000_000:
                raise ValueError("Expected one bounded wheel METADATA file")
            content = archive.read(entries[0])
    else:
        with tarfile.open(file, "r:gz") as source_archive:
            members = [item for item in source_archive.getmembers() if (
                len(PurePosixPath(item.name).parts) == 2 and item.name.endswith("/PKG-INFO")
            )]
            if len(members) != 1 or not members[0].isfile() or members[0].size > 1_000_000:
                raise ValueError("Expected one bounded sdist PKG-INFO file")
            stream = source_archive.extractfile(members[0])
            if stream is None:
                raise ValueError("Missing sdist package metadata")
            with stream:
                content = stream.read()
    metadata = BytesParser().parsebytes(content)
    return _string(metadata.get("Name")), _string(metadata.get("Version"))


def _check_packages(files: list[Path], version: str) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for file in files:
        name, package_version = _package_metadata(file)
        if re.sub(r"[-_.]+", "-", name).lower() != "arcade-agent" or package_version != version:
            raise ValueError("Distribution name/version does not match the arcade-agent release")
        filename_matches = (
            file.name.startswith(f"arcade_agent-{version}-")
            if file.name.endswith(".whl")
            else file.name in {f"arcade_agent-{version}.tar.gz", f"arcade-agent-{version}.tar.gz"}
        )
        if not filename_matches:
            raise ValueError("Distribution filename does not match its package metadata")
        hashes[file.name] = hashlib.sha256(file.read_bytes()).hexdigest()
    return hashes


def create(dist: Path, pyproject: Path, repository: str, sha: str) -> BuildManifest:
    with pyproject.open("rb") as source:
        version = _string(tomllib.load(source)["project"]["version"])
    _identity(repository, sha, version)
    manifest: BuildManifest = {
        "schema": 1,
        "repository": repository,
        "sha": sha,
        "version": version,
        "files": _check_packages(_distributions(dist), version),
    }
    (dist / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def verify(dist: Path, repository: str, sha: str) -> str:
    manifest_path = dist / MANIFEST
    if manifest_path.is_symlink():
        raise ValueError("Release manifest must not be a symlink")
    raw: object = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        not isinstance(raw, dict)
        or set(raw) != {"schema", "repository", "sha", "version", "files"}
        or type(raw["schema"]) is not int
        or raw["schema"] != 1
        or not isinstance(raw["files"], dict)
    ):
        raise ValueError("Invalid release manifest schema")
    version = _string(raw["version"])
    _identity(_string(raw["repository"]), _string(raw["sha"]), version)
    if raw["repository"] != repository or raw["sha"] != sha:
        raise ValueError("Artifact does not belong to the validated CI repository/commit")
    hashes: dict[str, str] = {}
    for key, value in raw["files"].items():
        filename, digest = _string(key), _string(value)
        if Path(filename).name != filename or not HASH.fullmatch(digest):
            raise ValueError("Invalid distribution filename/hash")
        hashes[filename] = digest
    if _check_packages(_distributions(dist), version) != hashes:
        raise ValueError("Distribution hashes do not match the CI build manifest")
    return version


def pypi_ready(dist: Path, response: Path, version: str) -> bool:
    raw: object = json.loads(response.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("urls"), list):
        raise ValueError("Invalid PyPI release response")
    remote: dict[str, str] = {}
    for item in raw["urls"]:
        if not isinstance(item, dict) or not isinstance(item.get("digests"), dict):
            raise ValueError("Invalid PyPI distribution metadata")
        filename = _string(item.get("filename"))
        digest = _string(item["digests"].get("sha256"))
        if filename in remote or not HASH.fullmatch(digest):
            raise ValueError("Ambiguous/invalid PyPI distribution hash")
        remote[filename] = digest
    missing = False
    for filename, digest in _check_packages(_distributions(dist), version).items():
        if filename not in remote:
            missing = True
        elif remote[filename] != digest:
            raise ValueError("PyPI already contains different bytes for this distribution")
    return missing


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["create", "verify", "pypi"])
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--pyproject", type=Path)
    parser.add_argument("--response", type=Path)
    args = parser.parse_args()
    if args.command == "create":
        if args.pyproject is None:
            parser.error("create requires --pyproject")
        version = create(args.dist, args.pyproject, args.repository, args.sha)["version"]
    else:
        version = verify(args.dist, args.repository, args.sha)
    ready: bool | None = None
    if args.command == "pypi":
        if args.response is None:
            parser.error("pypi requires --response")
        ready = pypi_ready(args.dist, args.response, version)
    print(f"Validated arcade-agent {version} at {args.sha}")
    if output := os.environ.get("GITHUB_OUTPUT"):
        with Path(output).open("a", encoding="utf-8") as target:
            target.write(f"version={version}\n")
            if ready is not None:
                target.write(f"ready={str(ready).lower()}\n")


if __name__ == "__main__":
    main()
