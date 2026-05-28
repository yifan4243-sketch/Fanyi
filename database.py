"""
SQLite 历史记录服务
"""

import sqlite3
import time
from pathlib import Path
from typing import Any

DB_DIR = Path(__file__).parent
DB_PATH = DB_DIR / "translation_history.db"


class Database:
    def __init__(self) -> None:
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        self._conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    def _create_tables(self) -> None:
        assert self._conn
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS translations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_text TEXT NOT NULL,
                translated_text TEXT NOT NULL,
                direction TEXT NOT NULL DEFAULT 'inbound',
                source_language TEXT DEFAULT 'auto',
                target_language TEXT DEFAULT 'zh-CN',
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            )
        """)
        self._conn.commit()

    def insert(
        self,
        source_text: str,
        translated_text: str,
        direction: str = "inbound",
        source_language: str = "auto",
        target_language: str = "zh-CN",
    ) -> int:
        assert self._conn
        cur = self._conn.execute(
            """INSERT INTO translations
               (source_text, translated_text, direction, source_language, target_language)
               VALUES (?, ?, ?, ?, ?)""",
            (source_text, translated_text, direction, source_language, target_language),
        )
        self._conn.commit()
        return cur.lastrowid or 0

    def get_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        assert self._conn
        cur = self._conn.execute(
            "SELECT * FROM translations ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = cur.fetchall()
        return [
            {
                "id": r[0],
                "source_text": r[1],
                "translated_text": r[2],
                "direction": r[3],
                "source_language": r[4],
                "target_language": r[5],
                "created_at": r[6],
            }
            for r in rows
        ]

    def cleanup_old(self, max_days: int = 30) -> int:
        assert self._conn
        cutoff = time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(time.time() - max_days * 86400)
        )
        cur = self._conn.execute(
            "DELETE FROM translations WHERE created_at < ?", (cutoff,)
        )
        self._conn.commit()
        return cur.rowcount

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
