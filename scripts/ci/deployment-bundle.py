"""Create the image-only release asset from an explicit list of runtime files."""

import argparse
import gzip
import io
import re
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FILES = (
    ".env.example",
    "docker-compose.yml",
    "scripts/install.sh",
    "scripts/start.sh",
    "scripts/deploy.sh",
    "scripts/common.sh",
    "scripts/healthcheck.sh",
    "scripts/ledger-check.sql",
    "scripts/status.sh",
    "scripts/logs.sh",
    "scripts/stop.sh",
    "scripts/collect-data.sh",
    "scripts/run-backtest.sh",
    "scripts/backup.sh",
    "scripts/archive_manifest.py",
    "scripts/restore.sh",
    "scripts/update.sh",
    "scripts/check-release-schema.py",
    "scripts/rollback.sh",
    "infrastructure/nginx/host.conf",
    "infrastructure/systemd/polyquant-backup.service",
    "infrastructure/systemd/polyquant-backup.timer",
    "infrastructure/prometheus/prometheus.yml",
    "infrastructure/grafana/datasources/prom.yml",
    "infrastructure/grafana/dashboards/provider.yml",
    "infrastructure/grafana/boards/research.json",
)


def bundle(version: str, prefix: str, destination: Path):
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("Expected a release version without the v prefix")
    prefix = prefix.lower()
    if not re.fullmatch(r"ghcr\.io/[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9._-]*", prefix):
        raise ValueError("Expected ghcr.io/owner/repository")
    settings = {"IMAGE_PREFIX": prefix, "APP_VERSION": version, "COLLECTOR_REPLICAS": "0"}
    with (
        destination.open("wb") as raw,
        gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as zipped,
        tarfile.open(fileobj=zipped, mode="w|") as archive,
    ):
        for name in FILES:
            path = ROOT / name
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"Deployment input must be a regular file: {name}")
            data = path.read_bytes()
            if name == ".env.example":
                template = data.decode()
                for key, value in settings.items():
                    template, count = re.subn(rf"^{key}=.*$", f"{key}={value}", template, flags=re.MULTILINE)
                    if count != 1:
                        raise ValueError(f"Expected one {key} in .env.example")
                data = template.encode()
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            entry.mode = 0o755 if name.endswith(".sh") else 0o644
            archive.addfile(entry, io.BytesIO(data))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    parser.add_argument("image_prefix")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    bundle(args.version, args.image_prefix, args.output)
