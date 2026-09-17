"""SQLite persistence: immutable digests, conversation, preferences and delivery queue."""

from contextlib import contextmanager
from datetime import date
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import time


def chunks(text: str, size: int = 1500) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)] or [""]


class Store:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS digests (
                    id TEXT PRIMARY KEY, day TEXT NOT NULL, body TEXT NOT NULL,
                    created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS replies (
                    id TEXT PRIMARY KEY, question TEXT NOT NULL, answer TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS outbox (
                    id TEXT PRIMARY KEY, body TEXT NOT NULL, created REAL NOT NULL,
                    lease_until REAL NOT NULL DEFAULT 0, lease_token TEXT,
                    delivered INTEGER NOT NULL DEFAULT 0);
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def put_digest(self, day: str, papers: list[dict]) -> dict:
        date.fromisoformat(day)
        body = json.dumps(papers, ensure_ascii=False, sort_keys=True)
        digest_id = day + "-" + hashlib.sha256(body.encode()).hexdigest()[:12]
        with self.connection() as db:
            db.execute("INSERT OR IGNORE INTO digests VALUES (?,?,?,?)",
                       (digest_id, day, body, time.time()))
        return self.digest(digest_id=digest_id)

    def digest(self, *, digest_id: str | None = None, day: str | None = None):
        with self.connection() as db:
            if digest_id:
                row = db.execute("SELECT * FROM digests WHERE id=?", (digest_id,)).fetchone()
            elif day:
                row = db.execute("SELECT * FROM digests WHERE day=? ORDER BY created DESC LIMIT 1",
                                 (day,)).fetchone()
            else:
                row = db.execute("SELECT * FROM digests ORDER BY day DESC, created DESC LIMIT 1").fetchone()
        return {"id": row["id"], "date": row["day"], "papers": json.loads(row["body"])} if row else None

    def get(self, key: str, default=None):
        with self.connection() as db:
            row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key: str, value):
        with self.connection() as db:
            db.execute("INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (key, json.dumps(value, ensure_ascii=False)))

    def cached_reply(self, message_id: str, question: str):
        with self.connection() as db:
            row = db.execute("SELECT * FROM replies WHERE id=?", (message_id,)).fetchone()
        if row and row["question"] != question:
            raise ValueError("同一消息 ID 不能对应不同的问题")
        return row["answer"] if row else None

    def save_reply(self, message_id: str, question: str, answer: str, deliver: bool):
        with self.connection() as db:
            db.execute("INSERT OR IGNORE INTO replies VALUES (?,?,?)", (message_id, question, answer))
            if deliver:
                self._enqueue(db, "reply:" + message_id, answer)

    def _enqueue(self, db, key: str, text: str):
        parts = chunks(text)
        for i, part in enumerate(parts):
            prefix = f"({i + 1}/{len(parts)}) " if len(parts) > 1 else ""
            db.execute("INSERT OR IGNORE INTO outbox(id,body,created) VALUES (?,?,?)",
                       (f"{key}:{i:04d}", prefix + part, time.time()))

    def enqueue(self, key: str, text: str):
        with self.connection() as db:
            self._enqueue(db, key, text)

    def claim(self):
        now, token = time.time(), secrets.token_urlsafe(24)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM outbox WHERE delivered=0 ORDER BY created,id LIMIT 1").fetchone()
            # Keep parts ordered, even while another bridge owns the oldest item.
            if not row or row["lease_until"] > now:
                return None
            db.execute("UPDATE outbox SET lease_until=?,lease_token=? WHERE id=?",
                       (now + 120, token, row["id"]))
        return {"id": row["id"], "text": row["body"], "lease_token": token}

    def acknowledge(self, item_id: str, token: str) -> bool:
        with self.connection() as db:
            result = db.execute("UPDATE outbox SET delivered=1 WHERE id=? AND lease_token=? AND delivered=0",
                                (item_id, token))
            ok = result.rowcount == 1
            if ok and item_id.startswith("digest:"):
                digest_id = item_id.split(":")[1]
                row = db.execute("SELECT value FROM state WHERE key='conversation'").fetchone()
                previous = json.loads(row[0]) if row else {}
                if previous.get("digest_id") != digest_id:
                    value = json.dumps({"digest_id": digest_id, "numbers": [], "history": []})
                    db.execute("INSERT INTO state VALUES ('conversation',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (value,))
            return ok
