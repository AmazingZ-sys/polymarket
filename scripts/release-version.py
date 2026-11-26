"""Calculate a SemVer from reachable tags and Conventional Commit headers."""

import json
import re
import subprocess
from pathlib import Path


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def next_version() -> str:
    tags = [
        (tuple(map(int, tag[1:].split("."))), tag)
        for tag in git("tag", "--merged", "HEAD").splitlines()
        if re.fullmatch(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", tag)
    ]
    if not tags:
        return json.loads(Path("package.json").read_text())["version"]
    (major, minor, patch), tag = max(tags)
    if git("rev-list", "-1", tag) == git("rev-parse", "HEAD"):
        return tag[1:]
    logs = git("log", "--format=%s%x00%b%x1e", tag + "..HEAD")
    commits = [entry.strip().partition("\0") for entry in logs.split("\x1e") if entry.strip()]
    if any(
        re.match(r"^[a-z]+(?:\([^)]+\))?!:", header) or re.search(r"^BREAKING[ -]CHANGE:\s*\S", body, re.MULTILINE)
        for header, _, body in commits
    ):
        return f"{major + 1}.0.0"
    if any(re.match(r"^feat(?:\([^)]+\))?:", header) for header, _, _ in commits):
        return f"{major}.{minor + 1}.0"
    if any(re.match(r"^(fix|perf|revert)(?:\([^)]+\))?:", header) for header, _, _ in commits):
        return f"{major}.{minor}.{patch + 1}"
    return ""


if __name__ == "__main__":
    print(next_version())
