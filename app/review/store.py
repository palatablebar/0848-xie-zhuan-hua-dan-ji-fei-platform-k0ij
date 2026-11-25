import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=8)
        connection.row_factory = sqlite3.Row
        return connection

    def init(self):
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS runs(
              id TEXT PRIMARY KEY,
              request_key TEXT UNIQUE NOT NULL,
              created_at TEXT NOT NULL,
              profile TEXT NOT NULL,
              status TEXT NOT NULL,
              revision INTEGER NOT NULL,
              payload TEXT NOT NULL,
              comment TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS run_versions(
              id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL,
              version INTEGER NOT NULL,
              kind TEXT NOT NULL,
              base_version INTEGER,
              request_key TEXT,
              digest TEXT NOT NULL,
              comment TEXT NOT NULL DEFAULT '',
              reason TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              payload TEXT NOT NULL,
              UNIQUE(run_id, version),
              UNIQUE(run_id, request_key),
              FOREIGN KEY(run_id) REFERENCES runs(id)
            );
            CREATE INDEX IF NOT EXISTS idx_run_versions_run ON run_versions(run_id, version);
            ''')

    def find_by_request(self, request_key):
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE request_key=?", (request_key,)).fetchone()
        return dict(row) if row else None

    def create(self, request_key, profile, payload):
        existing = self.find_by_request(request_key)
        if existing:
            return existing
        run_id = uuid.uuid4().hex[:12]
        with self.connect() as db:
            db.execute("INSERT INTO runs(id,request_key,created_at,profile,status,revision,payload) VALUES(?,?,?,?,?,?,?)",
                       (run_id, request_key, utc_now(), profile, "DRAFT", 0,
                        json.dumps(payload, ensure_ascii=False)))
        return self.get(run_id)

    def get(self, run_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def review(self, run_id, decision, comment, expected_revision):
        """签发或暂缓。APPROVED 时原子固化一个不可变版本快照。"""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                return None, None, "missing"
            if row["revision"] != expected_revision:
                return None, None, "stale"
            db.execute("UPDATE runs SET status=?, revision=revision+1, comment=? WHERE id=?",
                       (decision, comment, run_id))
            snapshot = None
            if decision == "APPROVED":
                snapshot = self._insert_version(
                    db, row, version=row["revision"] + 1, kind="ISSUE",
                    base_version=None, request_key=None, comment=comment, reason="")
        return self.get(run_id), snapshot, None

    def amend(self, run_id, payload, version, kind, base_version, request_key, comment, reason):
        """在事务内固化补正版本。

        返回 ``(row, snapshot, problem)``；``problem`` 为 ``missing`` 表示批次不存在，
        为 ``stale`` 表示基准版本已落后于当前生效版本，为 ``duplicate`` 表示同一补正请求
        已签发（此时返回历史快照，不新增版本）。
        """
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                return None, None, "missing"
            if request_key is not None:
                prior = db.execute(
                    "SELECT * FROM run_versions WHERE run_id=? AND request_key=?",
                    (run_id, request_key)).fetchone()
                if prior is not None:
                    return self.get(run_id), dict(prior), "duplicate"
            if row["revision"] != base_version:
                return None, None, "stale"
            snapshot = self._snapshot_payload(
                payload, row, version, kind, base_version, request_key, comment, reason)
            db.execute(
                "INSERT INTO run_versions(id,run_id,version,kind,base_version,request_key,digest,comment,reason,created_at,payload)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (snapshot["id"], row["id"], version, kind, base_version, request_key,
                 snapshot["digest"], comment, reason, snapshot["created_at"],
                 json.dumps(snapshot["payload"], ensure_ascii=False)))
            db.execute("UPDATE runs SET status='APPROVED', revision=?, payload=? WHERE id=?",
                       (version, json.dumps(payload, ensure_ascii=False), run_id))
        return self.get(run_id), snapshot, None

    def _insert_version(self, db, run_row, version, kind, base_version, request_key, comment, reason):
        payload = json.loads(run_row["payload"])
        snapshot = self._snapshot_payload(
            payload, run_row, version, kind, base_version, request_key, comment, reason)
        db.execute(
            "INSERT INTO run_versions(id,run_id,version,kind,base_version,request_key,digest,comment,reason,created_at,payload)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (snapshot["id"], run_row["id"], version, kind, base_version, request_key,
             snapshot["digest"], comment, reason, snapshot["created_at"],
             json.dumps(snapshot["payload"], ensure_ascii=False)))
        return snapshot

    def _snapshot_payload(self, payload, run_row, version, kind, base_version, request_key, comment, reason):
        from app.evidence.manifest import stable_digest

        snapshot = dict(payload)
        snapshot["run_id"] = run_row["id"]
        snapshot["review"] = {"status": "APPROVED", "revision": version, "comment": comment}
        version_block = {"version": version, "kind": kind, "base_version": base_version,
                         "request_key": request_key, "reason": reason, "comment": comment,
                         "created_at": utc_now()}
        snapshot["version_block"] = version_block
        digest = stable_digest(snapshot)
        version_block["digest"] = digest
        return {"id": uuid.uuid4().hex[:12], "run_id": run_row["id"], "version": version,
                "kind": kind, "base_version": base_version, "request_key": request_key,
                "digest": digest, "comment": comment, "reason": reason,
                "created_at": version_block["created_at"], "payload": snapshot}

    def get_version(self, run_id, version):
        with self.connect() as db:
            row = db.execute("SELECT * FROM run_versions WHERE run_id=? AND version=?",
                             (run_id, version)).fetchone()
        return self._version_row(row)

    def list_versions(self, run_id):
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM run_versions WHERE run_id=? ORDER BY version", (run_id,)).fetchall()
        return [self._version_row(row) for row in rows]

    def find_version_by_request(self, run_id, request_key):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM run_versions WHERE run_id=? AND request_key=?",
                (run_id, request_key)).fetchone()
        return self._version_row(row)

    @staticmethod
    def _version_row(row):
        if row is None:
            return None
        item = dict(row)
        item["payload"] = json.loads(item["payload"])
        return item
