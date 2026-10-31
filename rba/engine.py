"""Approval-bound document plans with immutable receipts and readback checks."""
from __future__ import annotations

import hashlib
import fcntl
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from functools import wraps
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

MAX_PAYLOAD_BYTES = 64_000
MAX_TTL_SECONDS = 3600
DEFAULT_TTL_SECONDS = 300


class ReviewError(ValueError):
    """Invalid plan, approval, or local action."""


class PermissionDenied(ReviewError):
    """The execution boundary denied an action."""


class StaleApproval(ReviewError):
    def __init__(self, message: str, receipt: dict):
        super().__init__(message)
        self.receipt = receipt


def _json(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@contextmanager
def _connection(path: Path, timeout: float = 5):
    db = sqlite3.connect(path, timeout=timeout)
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _serialized(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._operation_lock():
            return method(self, *args, **kwargs)
    return wrapped


@dataclass(frozen=True)
class Plan:
    plan_id: str
    version: int
    target_id: str
    target_name: str
    expected_revision: str
    expected_before: str
    payload: str
    fingerprint: str
    created_at: float


@dataclass(frozen=True)
class ApprovalReceipt:
    approval_id: str
    plan_id: str
    version: int
    fingerprint: str
    approved_at: float
    expires_at: float
    actor: str


@dataclass(frozen=True)
class ActionReceipt:
    receipt_id: str
    plan_id: str
    version: int
    approval_id: str
    fingerprint: str
    status: str
    tool_status: str
    target_id: str
    observed_revision: str | None
    observed_fingerprint: str | None
    detail: str
    created_at: float
    parent_receipt_id: str | None = None


class FakeDocumentAdapter:
    """A local SQLite destination with only two fabricated documents."""
    DOCUMENTS = {
        "DOC-001": ("Quarterly plan: draft for review.", "4", "Quarterly operations plan"),
        "DOC-002": ("Meeting notes: first draft.", "2", "Synthetic meeting notes"),
    }

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with _connection(path) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS documents(doc_id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL, revision TEXT NOT NULL)")
            for doc_id, (content, revision, title) in self.DOCUMENTS.items():
                db.execute("INSERT OR IGNORE INTO documents VALUES(?,?,?,?)", (doc_id, title, content, revision))

    def get(self, target_id: str) -> dict:
        if target_id not in self.DOCUMENTS:
            raise ReviewError("Destination is outside the local document fixture.")
        with _connection(self.path) as db:
            row = db.execute("SELECT doc_id,title,content,revision FROM documents WHERE doc_id=?", (target_id,)).fetchone()
        if row is None:
            raise ReviewError("Synthetic destination document is missing.")
        return {"target_id": row[0], "title": row[1], "content": row[2], "revision": row[3]}

    def apply(self, target_id: str, expected_revision: str, expected_before: str, payload: str) -> str:
        if target_id not in self.DOCUMENTS:
            raise ReviewError("Destination is outside the local document fixture.")
        next_revision = str(int(expected_revision) + 1)
        with _connection(self.path, timeout=5) as db:
            cursor = db.execute("UPDATE documents SET content=?, revision=? WHERE doc_id=? AND revision=? AND content=?",
                                (payload, next_revision, target_id, expected_revision, expected_before))
            if cursor.rowcount != 1:
                raise ReviewError("Destination changed after review. Prepare a new plan.")
        return next_revision


class ReviewEngine:
    """Versioned plans with policy checks at approve and execute boundaries."""

    def __init__(self, root: str | Path, clock: Callable[[], float] = time.time):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.db_path = self.root / "review.sqlite"
        self.adapter = FakeDocumentAdapter(self.root / "fake-destination.sqlite")
        self.clock = clock
        self._init_db()

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def _operation_lock(self):
        fd = os.open(self.root / ".operations.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def _init_db(self) -> None:
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
            CREATE TABLE IF NOT EXISTS plans(plan_id TEXT PRIMARY KEY, current_version INTEGER NOT NULL, created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS plan_versions(
              plan_id TEXT NOT NULL, version INTEGER NOT NULL, target_id TEXT NOT NULL,
              target_name TEXT NOT NULL, expected_revision TEXT NOT NULL, expected_before TEXT NOT NULL,
              payload TEXT NOT NULL, fingerprint TEXT NOT NULL, created_at REAL NOT NULL,
              PRIMARY KEY(plan_id,version));
            CREATE TABLE IF NOT EXISTS approval_receipts(
              approval_id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, version INTEGER NOT NULL,
              fingerprint TEXT NOT NULL, approved_at REAL NOT NULL, expires_at REAL NOT NULL, actor TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS action_receipts(
              receipt_id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, version INTEGER NOT NULL,
              approval_id TEXT NOT NULL, fingerprint TEXT NOT NULL, status TEXT NOT NULL,
              tool_status TEXT NOT NULL, target_id TEXT NOT NULL, observed_revision TEXT,
              observed_fingerprint TEXT, detail TEXT NOT NULL, created_at REAL NOT NULL,
              parent_receipt_id TEXT, UNIQUE(approval_id, receipt_id));
            CREATE INDEX IF NOT EXISTS action_receipts_by_approval ON action_receipts(approval_id,created_at);
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS plan_versions_no_update BEFORE UPDATE ON plan_versions BEGIN SELECT RAISE(ABORT,'plan versions are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS plan_versions_no_delete BEFORE DELETE ON plan_versions BEGIN SELECT RAISE(ABORT,'plan versions are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS approval_receipts_no_update BEFORE UPDATE ON approval_receipts BEGIN SELECT RAISE(ABORT,'approval receipts are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS approval_receipts_no_delete BEFORE DELETE ON approval_receipts BEGIN SELECT RAISE(ABORT,'approval receipts are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS action_receipts_no_update BEFORE UPDATE ON action_receipts BEGIN SELECT RAISE(ABORT,'action receipts are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS action_receipts_no_delete BEFORE DELETE ON action_receipts BEGIN SELECT RAISE(ABORT,'action receipts are immutable'); END;
            """)
            db.executemany("INSERT OR IGNORE INTO settings VALUES(?,?)", [
                ("approval_ttl_seconds", str(DEFAULT_TTL_SECONDS)),
                ("can_prepare", "1"), ("can_approve", "1"), ("can_execute", "1"), ("can_verify", "1")])

    @staticmethod
    def _fingerprint(plan_id: str, version: int, target_id: str, expected_revision: str,
                     expected_before: str, payload: str) -> str:
        return _hash(_json({"plan_id": plan_id, "version": version, "target_id": target_id,
                            "expected_revision": expected_revision, "expected_before": expected_before,
                            "payload": payload}))

    def _authorize(self, action: str) -> None:
        with self._connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (f"can_{action}",)).fetchone()
        if not row or row[0] != "1":
            raise PermissionDenied(f"Permission denied for {action}.")

    @_serialized
    def prepare(self, target_id: str, payload: str) -> Plan:
        self._authorize("prepare")
        if target_id not in FakeDocumentAdapter.DOCUMENTS:
            raise ReviewError("Choose one of the local synthetic documents.")
        if not isinstance(payload, str) or len(payload.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise ReviewError("Document text must be within the 64 KB limit.")
        before = self.adapter.get(target_id)
        plan_id, version, now = uuid.uuid4().hex, 1, self.clock()
        fp = self._fingerprint(plan_id, version, target_id, before["revision"], before["content"], payload)
        with self._connect() as db:
            db.execute("INSERT INTO plans VALUES(?,?,?)", (plan_id, version, now))
            db.execute("INSERT INTO plan_versions VALUES(?,?,?,?,?,?,?,?,?)",
                       (plan_id, version, target_id, before["title"], before["revision"], before["content"], payload, fp, now))
        return Plan(plan_id, version, target_id, before["title"], before["revision"], before["content"], payload, fp, now)

    @_serialized
    def revise(self, plan_id: str, expected_version: int, target_id: str, payload: str) -> Plan:
        self._authorize("prepare")
        if target_id not in FakeDocumentAdapter.DOCUMENTS:
            raise ReviewError("Choose one of the local synthetic documents.")
        if not isinstance(payload, str) or len(payload.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise ReviewError("Document text must be within the 64 KB limit.")
        with self._connect() as db:
            current = db.execute("SELECT current_version FROM plans WHERE plan_id=?", (plan_id,)).fetchone()
            if not current or current[0] != expected_version:
                raise ReviewError("Plan version changed. Refresh and review the latest version.")
            previous = db.execute("SELECT * FROM plan_versions WHERE plan_id=? AND version=?", (plan_id, expected_version)).fetchone()
            if previous["target_id"] == target_id and previous["payload"] == payload:
                raise ReviewError("No plan changes to save.")
            baseline = self.adapter.get(target_id)
            version, now = expected_version + 1, self.clock()
            fp = self._fingerprint(plan_id, version, target_id, baseline["revision"], baseline["content"], payload)
            db.execute("UPDATE plans SET current_version=? WHERE plan_id=?", (version, plan_id))
            db.execute("INSERT INTO plan_versions VALUES(?,?,?,?,?,?,?,?,?)",
                       (plan_id, version, target_id, baseline["title"], baseline["revision"], baseline["content"], payload, fp, now))
        return Plan(plan_id, version, target_id, baseline["title"], baseline["revision"], baseline["content"], payload, fp, now)

    def _plan(self, plan_id: str, version: int | None = None) -> tuple[Plan, int]:
        with self._connect() as db:
            current = db.execute("SELECT current_version FROM plans WHERE plan_id=?", (plan_id,)).fetchone()
            if not current:
                raise ReviewError("Plan not found.")
            selected = version if version is not None else current[0]
            row = db.execute("SELECT * FROM plan_versions WHERE plan_id=? AND version=?", (plan_id, selected)).fetchone()
        if not row:
            raise ReviewError("Plan version not found.")
        p = Plan(row["plan_id"], row["version"], row["target_id"], row["target_name"], row["expected_revision"],
                 row["expected_before"], row["payload"], row["fingerprint"], row["created_at"])
        return p, current[0]

    @_serialized
    def approve(self, plan_id: str, version: int, fingerprint: str, actor: str = "local-operator") -> ApprovalReceipt:
        self._authorize("approve")
        p, current_version = self._plan(plan_id, version)
        if current_version != version or p.fingerprint != fingerprint or self._recomputed(p) != p.fingerprint:
            raise ReviewError("Plan changed. Review the current version before approval.")
        destination = self.adapter.get(p.target_id)
        if destination["revision"] != p.expected_revision or destination["content"] != p.expected_before:
            raise ReviewError("Destination changed after preparation. Prepare a new plan before approval.")
        now = self.clock()
        with self._connect() as db:
            ttl = int(db.execute("SELECT value FROM settings WHERE key='approval_ttl_seconds'").fetchone()[0])
            receipt = ApprovalReceipt(uuid.uuid4().hex, plan_id, version, p.fingerprint, now, now + ttl, actor)
            db.execute("INSERT INTO approval_receipts VALUES(?,?,?,?,?,?,?)", tuple(asdict(receipt).values()))
        return receipt

    @staticmethod
    def _recomputed(p: Plan) -> str:
        return ReviewEngine._fingerprint(p.plan_id, p.version, p.target_id, p.expected_revision, p.expected_before, p.payload)

    def _approval(self, approval_id: str) -> ApprovalReceipt:
        with self._connect() as db:
            row = db.execute("SELECT * FROM approval_receipts WHERE approval_id=?", (approval_id,)).fetchone()
        if not row:
            raise ReviewError("Approval receipt not found.")
        return ApprovalReceipt(row["approval_id"], row["plan_id"], row["version"], row["fingerprint"],
                               row["approved_at"], row["expires_at"], row["actor"])

    def _receipt(self, approval: ApprovalReceipt, status: str, tool_status: str, target_id: str,
                 detail: str, observed_revision: str | None = None, observed_fingerprint: str | None = None,
                 parent_receipt_id: str | None = None) -> ActionReceipt:
        receipt = ActionReceipt(uuid.uuid4().hex, approval.plan_id, approval.version, approval.approval_id,
                                approval.fingerprint, status, tool_status, target_id, observed_revision,
                                observed_fingerprint, detail, self.clock(), parent_receipt_id)
        with self._connect() as db:
            db.execute("INSERT INTO action_receipts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", tuple(asdict(receipt).values()))
        return receipt

    @_serialized
    def execute(self, approval_id: str, readback: bool = True) -> ActionReceipt:
        self._authorize("execute")
        if readback:
            self._authorize("verify")
        approval = self._approval(approval_id)
        with self._connect() as db:
            existing = db.execute("SELECT * FROM action_receipts WHERE approval_id=? ORDER BY created_at DESC LIMIT 1", (approval_id,)).fetchone()
        if existing:
            return ActionReceipt(**dict(existing))
        p, current_version = self._plan(approval.plan_id, approval.version)
        if current_version != approval.version or p.fingerprint != approval.fingerprint or self._recomputed(p) != p.fingerprint:
            return self._receipt(approval, "rejected", "not_started", p.target_id, "Plan version or fingerprint changed after approval.")
        if self.clock() >= approval.expires_at:
            return self._receipt(approval, "rejected", "not_started", p.target_id, "Approval expired before execution.")
        current = self.adapter.get(p.target_id)
        expected_revision = str(int(p.expected_revision) + 1)
        if current["revision"] == p.expected_revision and current["content"] == p.expected_before:
            try:
                self.adapter.apply(p.target_id, p.expected_revision, p.expected_before, p.payload)
            except ReviewError as exc:
                return self._receipt(approval, "rejected", "not_started", p.target_id, str(exc), current["revision"], _hash(current["content"]))
        elif not (current["revision"] == expected_revision and current["content"] == p.payload):
            return self._receipt(approval, "rejected", "not_started", p.target_id,
                                 "Destination changed after review. Prepare a new plan.", current["revision"], _hash(current["content"]))
        if not readback:
            return self._receipt(approval, "unverified", "success", p.target_id,
                                 "The adapter reported success. Destination readback was not run.")
        observed = self.adapter.get(p.target_id)
        matches = observed["revision"] == expected_revision and observed["content"] == p.payload
        return self._receipt(approval, "verified" if matches else "unverified", "success", p.target_id,
                             "Destination readback matches the reviewed plan." if matches else "Adapter reported success, but destination readback did not match.",
                             observed["revision"], _hash(observed["content"]))

    @_serialized
    def verify(self, receipt_id: str) -> ActionReceipt:
        self._authorize("verify")
        with self._connect() as db:
            action = db.execute("SELECT * FROM action_receipts WHERE receipt_id=? AND tool_status='success'", (receipt_id,)).fetchone()
        if not action:
            raise ReviewError("Successful action receipt not found.")
        if action["status"] == "verified":
            return ActionReceipt(**dict(action))
        approval = self._approval(action["approval_id"])
        p, _ = self._plan(action["plan_id"], action["version"])
        observed = self.adapter.get(action["target_id"])
        expected_revision = str(int(p.expected_revision) + 1)
        matches = observed["revision"] == expected_revision and observed["content"] == p.payload
        return self._receipt(approval, "verified" if matches else "unverified", "success", action["target_id"],
                             "Destination readback matches the reviewed plan." if matches else "Destination readback does not match the reviewed plan.",
                             observed["revision"], _hash(observed["content"]), receipt_id)

    @_serialized
    def update_settings(self, values: dict) -> dict:
        allowed = {"can_prepare", "can_approve", "can_execute", "can_verify", "approval_ttl_seconds"}
        if set(values) - allowed:
            raise ReviewError("Unsupported setting.")
        for key in ("can_prepare", "can_approve", "can_execute", "can_verify"):
            if key in values and not isinstance(values[key], bool):
                raise ReviewError(f"{key} must be a boolean.")
        if "approval_ttl_seconds" in values and (not isinstance(values["approval_ttl_seconds"], int) or not 30 <= values["approval_ttl_seconds"] <= MAX_TTL_SECONDS):
            raise ReviewError("Approval lifetime must be between 30 and 3600 seconds.")
        with self._connect() as db:
            for key, value in values.items():
                db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, str(int(value)) if isinstance(value, bool) else str(value)))
        return self.settings()

    def settings(self) -> dict:
        with self._connect() as db:
            values = dict(db.execute("SELECT key,value FROM settings"))
        return {"approval_ttl_seconds": int(values["approval_ttl_seconds"]), **{k: values[k] == "1" for k in ("can_prepare", "can_approve", "can_execute", "can_verify")}}

    def state(self) -> dict:
        with self._connect() as db:
            plans = [dict(r) for r in db.execute("SELECT p.plan_id,p.current_version,v.target_id,v.target_name,v.expected_revision,v.expected_before,v.payload,v.fingerprint,v.created_at FROM plans p JOIN plan_versions v ON p.plan_id=v.plan_id AND p.current_version=v.version ORDER BY p.created_at DESC LIMIT 50")]
            approvals = [dict(r) for r in db.execute("SELECT * FROM approval_receipts ORDER BY approved_at DESC LIMIT 25")]
            receipts = [dict(r) for r in db.execute("SELECT * FROM action_receipts ORDER BY created_at DESC LIMIT 50")]
        documents = [self.adapter.get(doc_id) for doc_id in FakeDocumentAdapter.DOCUMENTS]
        for d in documents:
            d.pop("content")
        return {"plans": plans, "approvals": approvals, "receipts": receipts, "documents": documents, "settings": self.settings()}
