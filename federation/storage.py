"""Each twin owns its history; HTTP readers open independent SQLite connections."""
from contextlib import closing
import json
import sqlite3
from pathlib import Path


class ShadowStore:
    def __init__(self, path, taz):
        self.path, self.taz = Path(path), taz
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY, payload TEXT NOT NULL)")

    def append(self, event):
        if event["taz"] != self.taz:
            raise ValueError("A twin cannot store another TAZ's observation")
        self.db.execute("INSERT INTO events VALUES (?, ?)", (event["seq"], json.dumps(event)))
        self.db.commit()

    def acknowledge(self, seq, feedback):
        row = self.db.execute("SELECT payload FROM events WHERE seq=?", (seq,)).fetchone()
        if row is None:
            raise ValueError("Feedback has no corresponding observation")
        event = json.loads(row[0])
        event['feedback'] = feedback
        self.db.execute("UPDATE events SET payload=? WHERE seq=?", (json.dumps(event), seq))
        self.db.commit()

    def close(self):
        self.db.close()


def latest(path):
    with closing(sqlite3.connect(path)) as db:
        row = db.execute("SELECT payload FROM events ORDER BY seq DESC LIMIT 1").fetchone()
    return json.loads(row[0]) if row else {}
