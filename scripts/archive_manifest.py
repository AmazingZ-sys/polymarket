"""Extract immutable archive references from a pg_dump snapshot and verify their bytes."""

import argparse
import hashlib
import json
import re
import sys
import tarfile
from pathlib import Path, PurePosixPath


def from_postgres(lines):
    fields = None
    manifest = {}
    for line in lines:
        if fields is None:
            match = re.match(r"^COPY public\.dataset_versions \((.+)\) FROM stdin;", line)
            if match:
                fields = match[1].split(", ")
            continue
        if line.rstrip("\n") == r"\.":
            return manifest
        values = line.rstrip("\n").split("\t")
        if len(values) != len(fields):
            raise ValueError("Unexpected dataset_versions COPY record")
        row = dict(zip(fields, values))
        name, digest = row["file_path"], row["sha256"]
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*\.parquet", name) or name in manifest:
            raise ValueError("Invalid or duplicate Parquet filename in the database snapshot")
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError(f"Invalid Parquet digest for {name}")
        manifest[name] = digest
    raise ValueError("Missing dataset_versions COPY data in archive")


def verify(bundle, manifest):
    seen = set()
    with tarfile.open(bundle, "r|gz") as archive:
        for entry in archive:
            path = PurePosixPath(entry.name)
            if entry.isdir() and str(path) == ".":
                continue
            name = str(path)
            if not entry.isfile() or len(path.parts) != 1 or name not in manifest or name in seen:
                raise ValueError(f"Unexpected archive entry {entry.name}")
            member = archive.extractfile(entry)
            digest = hashlib.sha256()
            while block := member.read(1024 * 1024):
                digest.update(block)
            if digest.hexdigest() != manifest[name]:
                raise ValueError(f"Parquet checksum mismatch for {name}")
            seen.add(name)
    if seen != set(manifest):
        raise ValueError("Parquet bundle missing a file referenced by dataset_versions")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["manifest", "names", "verify"])
    parser.add_argument("file", nargs="?")
    parser.add_argument("bundle", nargs="?")
    args = parser.parse_args()
    if args.action == "manifest":
        print(json.dumps(from_postgres(sys.stdin), sort_keys=True))
    else:
        expected = json.loads(Path(args.file).read_text())
        if args.action == "names":
            for name in sorted(expected):
                print(name)
        else:
            verify(args.bundle, expected)
