"""SQLite storage: conversations + messages."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL DEFAULT 'untitled',
    system_prompt TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT 'default',
    model TEXT NOT NULL DEFAULT '',
    thinking TEXT NOT NULL DEFAULT 'off',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    hidden INTEGER NOT NULL DEFAULT 0,
    summary INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ---- conversations ----
    def new_conversation(self, title="untitled", system_prompt="", provider="default", model="", thinking="off") -> int:
        t = now()
        cur = self.conn.execute(
            "INSERT INTO conversations(title, system_prompt, provider, model, thinking, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
            (title, system_prompt, provider, model, thinking, t, t),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_conversation(self, cid: int):
        return self.conn.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()

    def list_conversations(self, limit=50):
        return self.conn.execute(
            "SELECT * FROM conversations ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()

    def update_conversation(self, cid: int, **fields):
        fields["updated_at"] = now()
        keys = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE conversations SET {keys} WHERE id=?", (*fields.values(), cid))
        self.conn.commit()

    def delete_conversation(self, cid: int):
        self.conn.execute("DELETE FROM messages WHERE conversation_id=?", (cid,))
        self.conn.execute("DELETE FROM conversations WHERE id=?", (cid,))
        self.conn.commit()

    def latest_conversation(self):
        return self.conn.execute("SELECT * FROM conversations ORDER BY updated_at DESC LIMIT 1").fetchone()

    # ---- messages ----
    def add_message(self, cid: int, role: str, content, hidden=0, summary=0, at: int | None = None) -> int:
        row = (cid, role, json.dumps(content, ensure_ascii=False), hidden, summary, now())
        if at is not None:
            self.conn.execute("UPDATE messages SET id=id+1 WHERE conversation_id=? AND id>=?", (cid, at))
            self.conn.execute(
                "INSERT INTO messages(id, conversation_id, role, content, hidden, summary, created_at) VALUES(?,?,?,?,?,?,?)",
                (at, *row),
            )
            self.conn.commit()
            return at
        cur = self.conn.execute(
            "INSERT INTO messages(conversation_id, role, content, hidden, summary, created_at) VALUES(?,?,?,?,?,?)",
            row,
        )
        self.conn.commit()
        return cur.lastrowid

    def get_messages(self, cid: int, include_hidden=False):
        q = "SELECT * FROM messages WHERE conversation_id=?"
        q += "" if include_hidden else " AND hidden=0"
        q += " ORDER BY id"
        return self.conn.execute(q, (cid,)).fetchall()

    def mark_hidden(self, cid: int, ids):
        if not ids:
            return
        marks = ",".join("?" * len(ids))
        self.conn.execute(
            f"UPDATE messages SET hidden=1 WHERE conversation_id=? AND id IN ({marks})", (cid, *ids)
        )
        self.conn.commit()

    def clear_messages(self, cid: int):
        self.conn.execute("DELETE FROM messages WHERE conversation_id=?", (cid,))
        self.conn.commit()

    def wipe_all(self):
        self.conn.execute("DELETE FROM messages")
        self.conn.execute("DELETE FROM conversations")
        self.conn.commit()

    def close(self):
        self.conn.close()
