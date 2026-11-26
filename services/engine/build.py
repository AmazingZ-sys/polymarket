"""Content-addressed compatibility gate shared by live processing and replay."""

import hashlib
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def engine_hash() -> str:
    root = Path(__file__).resolve().parents[2]
    sources = sorted((root / "services/engine").glob("*.py"))
    # Economic SQL migrations enter the build; the digest registration migration
    # itself contains this hash and must remain outside the digest.
    sources += [root / "database/migrations" / n for n in ("020_engine.sql", "021_research_config.sql")]
    h = hashlib.sha256()
    for path in sources:
        h.update(path.relative_to(root).as_posix().encode() + b"\0")
        h.update(hashlib.sha256(path.read_bytes()).digest())
    return h.hexdigest()


def supports(config: dict) -> bool:
    expected = engine_hash()
    return config.get("implementation_hash") == expected and all(
        entry.get("implementation_hash") == expected for entry in config.get("strategies", [])
    )
