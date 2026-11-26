"""PostgreSQL-backed maintenance scheduler and notification outbox."""

import logging
import os
import shutil
import signal
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from pqcollector.archive import archive
from services.engine.execution import halt, reconcile

LOG = logging.getLogger("maintenance")
STOP = False


def notice(db, key, message):
    channels = []
    if os.getenv("WEBHOOK_URL"):
        channels.append("webhook")
    if os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
        channels.append("telegram")
    for channel in channels:
        db.execute(
            """INSERT INTO notification_logs(channel,dedup_key,message) VALUES(%s,%s,%s)
                      ON CONFLICT DO NOTHING""",
            (channel, key, "[PolyQuant 模拟环境] " + message),
        )


def send_one(db):
    row = db.execute("""SELECT * FROM notification_logs n WHERE n.status='PENDING' AND next_attempt_at<=now()
       AND NOT EXISTS (SELECT FROM notification_logs s WHERE s.channel=n.channel AND s.status='SENT'
       AND s.next_attempt_at>now()-interval '3 seconds')
       ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1""").fetchone()
    if not row:
        return
    attempts = row["attempts"] + 1
    try:
        if row["channel"] == "telegram":
            url = "https://api.telegram.org/bot" + os.environ["TELEGRAM_BOT_TOKEN"] + "/sendMessage"
            payload = {"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": row["message"]}
        else:
            url = os.environ["WEBHOOK_URL"]
            payload = {"event_id": row["dedup_key"], "message": row["message"]}
        if not url.startswith("https://"):
            raise ValueError("HTTPS required")
        httpx.post(url, json=payload, timeout=6).raise_for_status()
        status = "SENT"
    except (httpx.HTTPError, KeyError, ValueError):
        status = "FAILED" if attempts >= 6 else "PENDING"
    delay = 0 if status == "SENT" else min(3600, 2**attempts * 10)
    db.execute(
        """UPDATE notification_logs SET attempts=%s,status=%s,next_attempt_at=now()+%s*interval '1 second'
                  WHERE id=%s""",
        (attempts, status, delay, row["id"]),
    )


def events(db):
    streams = {
        "paper_sessions": ("id", "status"),
        "risk_events": ("id", "reason"),
        "system_events": ("id", "message"),
    }
    for table, (key, label) in streams.items():
        records = db.execute(
            f"""SELECT {key} id,{label} label FROM {table} WHERE {key}>
            coalesce((SELECT last_id FROM operation_cursors WHERE name=%s),0) ORDER BY {key} LIMIT 100""",
            (table,),
        ).fetchall()
        for row in records:
            notice(db, f"{table}/{row['id']}", f"{table} · {row['label']}")
        if records:
            db.execute(
                """INSERT INTO operation_cursors VALUES(%s,%s) ON CONFLICT(name) DO UPDATE
                SET last_id=excluded.last_id""",
                (table, records[-1]["id"]),
            )


def run_job(db, name):
    if name == "session_cleanup":
        db.execute("DELETE FROM user_sessions WHERE expires_at<=now()")
    elif name == "event_notifications":
        events(db)
    elif name == "archive":
        archive(db, Path(os.getenv("ARCHIVE_DIR", "/data/parquet")))
    elif name == "strategy_review":
        since = datetime.now(UTC).date().isoformat()
        candidates = db.execute("""SELECT a.strategy_id,a.strategy_version,r.reason,count(*) n,min(r.id) first_id,max(r.id) last_id
            FROM risk_events r JOIN paper_accounts a ON a.id=r.account_id WHERE a.mode='PAPER' AND r.created_at>=now()-interval '1 day'
            GROUP BY a.strategy_id,a.strategy_version,r.reason HAVING count(*)>=3""").fetchall()
        for row in candidates:
            db.execute(
                """INSERT INTO improvement_proposals(strategy_id,problem,evidence,hypothesis,acceptance,details,automated_key)
                VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(automated_key) DO NOTHING""",
                (
                    row["strategy_id"],
                    row["reason"],
                    f"{row['n']} risk events, IDs {row['first_id']}–{row['last_id']}",
                    "在相同市场与时段并行复查重复熔断的来源，比较基准与挑战者的观测覆盖。",
                    "按预先登记的完整市场样本比较缺口、未成交和净成本，保留风险限额。",
                    Jsonb(
                        {
                            "strategy_version": row["strategy_version"],
                            "affected_samples": row["n"],
                            "risk": "相关市场可能重复计数",
                        }
                    ),
                    f"{since}/{row['strategy_id']}/{row['strategy_version']}/{row['reason']}",
                ),
            )
    elif name == "ledger_check":
        for account in db.execute("""SELECT * FROM paper_accounts WHERE mode<>'BACKTEST'
                                   AND status NOT IN ('ARCHIVED','RISK_HALTED') ORDER BY id FOR UPDATE"""):
            if not reconcile(db, account["id"]):
                halt(db, account, "LEDGER_MISMATCH", datetime.now(UTC))
    elif name == "system_health":
        disk = shutil.disk_usage(os.getenv("ARCHIVE_DIR", "/tmp"))
        low = disk.free < max(1024**3, disk.total // 20)
        detail = {"disk_free": disk.free, "disk_total": disk.total, "load_average": os.getloadavg()}
        if Path("/proc/meminfo").is_file():
            detail["mem_available_kib"] = int(
                next(
                    line
                    for line in Path("/proc/meminfo").read_text().splitlines()
                    if line.startswith("MemAvailable:")
                ).split()[1]
            )
        db.execute(
            """INSERT INTO system_settings(name,value) VALUES('health',%s) ON CONFLICT(name)
                    DO UPDATE SET value=excluded.value,updated_at=now()""",
            (Jsonb(detail),),
        )
        if low:
            notice(db, "disk/" + datetime.now(UTC).strftime("%Y%m%d%H"), "归档磁盘剩余空间不足")
    elif name in {"daily_report", "weekly_report", "monthly_report"}:
        unit = {"daily_report": "day", "weekly_report": "week", "monthly_report": "month"}[name]
        report = db.execute(
            """SELECT pq_report(NULL,date_trunc(%s,now())-('1 '||%s)::interval,
                           date_trunc(%s,now()),%s) report""",
            (unit, unit, unit, name),
        ).fetchone()["report"]
        notice(
            db,
            name + "/" + report["id"],
            name + " · " + report["start_time"] + " · " + report["result"]["validation"],
        )
    else:
        raise ValueError("unknown job")


def once(db):
    with db.transaction():
        job = db.execute("""SELECT name FROM scheduled_jobs WHERE next_run_at<=now()
                          ORDER BY next_run_at,name LIMIT 1 FOR UPDATE SKIP LOCKED""").fetchone()
        if job:
            try:
                with db.transaction():
                    run_job(db, job["name"])
                message, failed = "OK", 0
            except (OSError, ValueError, RuntimeError, psycopg.Error) as err:
                message, failed = type(err).__name__, 1
                LOG.error("job %s failed (%s)", job["name"], message)
            db.execute(
                """UPDATE scheduled_jobs SET last_run_at=now(),last_result=%s,failures=failures+%s,
                next_run_at=now()+interval_seconds*interval '1 second' WHERE name=%s""",
                (message, failed, job["name"]),
            )
    with db.transaction():
        if db.execute("SELECT pg_try_advisory_xact_lock(7490321) ok").fetchone()["ok"]:
            send_one(db)


def finish(*_):
    global STOP
    STOP = True


def main():
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, finish)
    while not STOP:
        try:
            with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row, autocommit=True) as db:
                while not STOP:
                    once(db)
                    Path("/tmp/pq-ops-alive").touch()
                    time.sleep(1)
        except psycopg.Error as err:
            LOG.error("database unavailable (%s)", type(err).__name__)
            time.sleep(3)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
