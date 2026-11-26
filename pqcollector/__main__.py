import argparse
import asyncio
import json
import logging
import os
import signal
from pathlib import Path

import psycopg

from .archive import archive, raw_event
from .history import export, import_new
from .runtime import run
from .store import Store, connect


async def collect(dsn, seconds):
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    delay = 1
    while not stop.is_set():
        try:
            with connect(dsn) as db:
                session = asyncio.create_task(run(Store(db), seconds))
                signal_wait = asyncio.create_task(stop.wait())
                try:
                    done, _ = await asyncio.wait({session, signal_wait}, return_when=asyncio.FIRST_COMPLETED)
                    if session in done:
                        session.result()
                        return
                finally:
                    for t in (session, signal_wait):
                        t.cancel()
                    await asyncio.gather(session, signal_wait, return_exceptions=True)
        except psycopg.OperationalError:
            if seconds is not None:
                raise
            logging.getLogger("pqcollector").warning("Database unavailable, reconnect in %ss", delay)
            try:
                await asyncio.wait_for(stop.wait(), delay)
            except TimeoutError:
                pass
            delay = min(delay * 2, 60)


def main():
    parser = argparse.ArgumentParser(description="PolyQuant public market collector")
    parser.add_argument(
        "command", nargs="?", default="run", choices=["run", "once", "archive", "raw", "export", "import"]
    )
    parser.add_argument("--seconds", type=int, default=10)
    parser.add_argument("--directory", type=Path, default=Path(os.getenv("ARCHIVE_DIR", "/data/parquet")))
    parser.add_argument("--event-id", help="RAW source record identifier")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--sha256")
    parser.add_argument("--database-name")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        parser.error("DATABASE_URL is required")
    if args.seconds < 1:
        parser.error("--seconds must be positive")
    if args.command == "import":
        if not all((args.manifest, args.sha256, args.database_name)):
            parser.error("import requires --manifest, --sha256, and --database-name")
        print(json.dumps(import_new(dsn, args.database_name, args.manifest, args.sha256)))
    elif args.command == "export":
        with connect(dsn) as db:
            print(json.dumps(export(db, args.directory)))
    elif args.command == "raw":
        if not args.event_id:
            parser.error("--event-id is required for raw")
        with connect(dsn) as db:
            record = raw_event(db, args.directory, args.event_id)
            if record is None:
                parser.exit(1, "Unknown raw event\n")
            print(json.dumps(record, default=str))
    elif args.command == "archive":
        with connect(dsn) as db:
            print(json.dumps(archive(db, args.directory)))
    else:
        asyncio.run(collect(dsn, args.seconds if args.command == "once" else None))


if __name__ == "__main__":
    main()
