from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, allow_nan=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


class BusyError(Exception):
    pass


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL,
                    config TEXT NOT NULL, state TEXT NOT NULL, stage TEXT NOT NULL DEFAULT 'queued',
                    message TEXT NOT NULL DEFAULT '', intent TEXT NOT NULL DEFAULT '',
                    progress REAL NOT NULL DEFAULT 0, created REAL NOT NULL, updated REAL NOT NULL,
                    spent REAL NOT NULL DEFAULT 0, reserved REAL NOT NULL DEFAULT 0,
                    result TEXT NOT NULL DEFAULT '{}'
                );
                DROP INDEX IF EXISTS one_active;
                CREATE TABLE IF NOT EXISTS preferences(key TEXT PRIMARY KEY, value INTEGER NOT NULL);
                INSERT OR IGNORE INTO preferences VALUES('max_concurrent_jobs', 1);
                CREATE TABLE IF NOT EXISTS layouts(id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS layout_frames(
                    layout_id TEXT PRIMARY KEY REFERENCES layouts(id) ON DELETE CASCADE,
                    name TEXT NOT NULL, image BLOB NOT NULL, width INTEGER, height INTEGER
                );
                CREATE TABLE IF NOT EXISTS clips(
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                    revision INTEGER NOT NULL, body TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS clip_reviews(
                    clip_id TEXT PRIMARY KEY REFERENCES clips(id),
                    revision INTEGER NOT NULL, status TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS requests(
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                    amount REAL NOT NULL, actual REAL, status TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS request_usage(
                    request_id TEXT PRIMARY KEY REFERENCES requests(id), body TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS youtube_channels(id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS youtube_videos(
                    id TEXT PRIMARY KEY, channel_id TEXT NOT NULL, published TEXT NOT NULL, body TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS youtube_channel_date ON youtube_videos(channel_id, published);
                PRAGMA user_version=1;
            """)
            db.execute("BEGIN IMMEDIATE")
            frame_columns = {row["name"] for row in db.execute("PRAGMA table_info(layout_frames)")}
            for column in ("width", "height"):
                if column not in frame_columns:
                    db.execute(f"ALTER TABLE layout_frames ADD COLUMN {column} INTEGER")
            review_columns = {row["name"] for row in db.execute("PRAGMA table_info(clip_reviews)")}
            if "note" not in review_columns:
                db.execute("ALTER TABLE clip_reviews ADD COLUMN note TEXT NOT NULL DEFAULT ''")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def unpack(row):
        if row is None:
            raise KeyError("Not found")
        value = dict(row)
        for field in ("config", "result", "body"):
            if field in value:
                value[field] = json.loads(value[field])
        return value

    def get(self, run_id):
        with self.connect() as db:
            return self.unpack(db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())

    def runs(self):
        with self.connect() as db:
            return [self.unpack(r) for r in db.execute("SELECT * FROM runs ORDER BY created DESC LIMIT 100")]

    def channel(self, channel_id):
        with self.connect() as db:
            row = db.execute("SELECT body FROM youtube_channels WHERE id=?", (channel_id,)).fetchone()
            return json.loads(row["body"]) if row else None

    def save_video_page(self, channel, videos):
        with self.connect() as db:
            for video in videos:
                db.execute(
                    "INSERT INTO youtube_videos VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                    "channel_id=excluded.channel_id,published=excluded.published,body=excluded.body",
                    (video["id"], channel["id"], video["published"], json.dumps(video, ensure_ascii=False)),
                )
            db.execute(
                "INSERT INTO youtube_channels VALUES(?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                (channel["id"], json.dumps(channel, ensure_ascii=False)),
            )

    def videos(self, channel_id):
        with self.connect() as db:
            db.execute("BEGIN")
            videos = [
                json.loads(r["body"])
                for r in db.execute(
                    "SELECT body FROM youtube_videos WHERE channel_id=? ORDER BY published DESC,id",
                    (channel_id,),
                )
            ]
            history = {}
            # Use all matching runs, not the recent-runs UI's 100-row limit.
            for row in db.execute(
                "SELECT r.id,r.state,r.result,json_extract(r.config,'$.video') AS video "
                "FROM runs r JOIN youtube_videos v ON v.id=json_extract(r.config,'$.video') "
                "WHERE v.channel_id=? ORDER BY r.created DESC,r.rowid DESC",
                (channel_id,),
            ):
                result = json.loads(row["result"])
                item = history.setdefault(
                    row["video"],
                    {
                        "run_id": row["id"],
                        "state": row["state"],
                        "outcome": result.get("outcome"),
                        "processed": False,
                        "completed_run_id": None,
                    },
                )
                if (row["state"] == "completed" and result.get("coverage") != "partial") or result.get(
                    "coverage"
                ) == "complete":
                    item["processed"] = True
                    if item["completed_run_id"] is None:
                        item["completed_run_id"] = row["id"]
        return [
            {
                **v,
                **history.get(
                    v["id"],
                    {
                        "run_id": None,
                        "state": "not_started",
                        "outcome": None,
                        "processed": False,
                        "completed_run_id": None,
                    },
                ),
            }
            for v in videos
        ]

    def admit(self, config: dict, request_key: str) -> str:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT id,config FROM runs WHERE request_key=?", (request_key,)).fetchone()
            if old:
                if json.loads(old["config"]) != config:
                    raise ValueError("This request key belongs to a different run.")
                return old["id"]
            run_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO runs(id,request_key,config,state,created,updated) VALUES(?,?,?,'queued',?,?)",
                (run_id, request_key, json.dumps(config), time.time(), time.time()),
            )
            return run_id

    def update(self, run_id, **fields):
        allowed = {"state", "stage", "message", "intent", "progress", "result"}
        if not fields.keys() <= allowed:
            raise ValueError("Invalid update")
        fields["updated"] = time.time()
        if "result" in fields:
            fields["result"] = json.dumps(fields["result"], ensure_ascii=False)
        with self.connect() as db:
            db.execute(
                "UPDATE runs SET " + ",".join(f"{k}=?" for k in fields) + " WHERE id=?",
                (*fields.values(), run_id),
            )

    def control(self, run_id, action):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            run = self.unpack(db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())
            state, intent = run["state"], run["intent"]
            stage, result = run["stage"], run["result"]
            if action == "review-all" and state == "completed":
                state, intent, stage = "queued", "", "review-priority"
                result = {**result, "review_all_requested": True}
            elif action == "recheck" and state == "completed":
                state, intent, stage = "queued", "", "recheck"
                result = {**result, "selection_recheck_requested": True}
            elif action == "resume" and state in {"paused", "failed", "cancelled"}:
                state, intent = "queued", ""
            elif action in {"pause", "cancel"} and state in {"queued", "running", "paused"}:
                if state == "running":
                    intent = action
                else:
                    state, intent = ("paused" if action == "pause" else "cancelled"), ""
            else:
                raise ValueError("This action is unavailable for the current run.")
            db.execute(
                "UPDATE runs SET state=?,intent=?,stage=?,result=?,updated=? WHERE id=?",
                (state, intent, stage, json.dumps(result), time.time(), run_id),
            )

    def recover(self):
        # Called only while holding the OS worker singleton; previous owned children have died.
        with self.connect() as db:
            db.execute(
                "UPDATE runs SET state=CASE intent WHEN 'cancel' THEN 'cancelled' "
                "WHEN 'pause' THEN 'paused' ELSE 'queued' END,intent='' WHERE state='running'"
            )

    def concurrency(self):
        with self.connect() as db:
            return db.execute("SELECT value FROM preferences WHERE key='max_concurrent_jobs'").fetchone()[0]

    def set_concurrency(self, value):
        if type(value) is not int or not 1 <= value <= 4:
            raise ValueError("Choose 1–4 concurrent jobs.")
        with self.connect() as db:
            db.execute("UPDATE preferences SET value=? WHERE key='max_concurrent_jobs'", (value,))

    def claim(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            limit = db.execute("SELECT value FROM preferences WHERE key='max_concurrent_jobs'").fetchone()[0]
            running = db.execute("SELECT COUNT(*) FROM runs WHERE state='running'").fetchone()[0]
            if running >= limit:
                return None
            row = db.execute("SELECT id FROM runs WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE runs SET state='running',updated=? WHERE id=?", (time.time(), row["id"]))
                return row["id"]
        return None

    def reserve(self, run_id, amount, cap, metadata=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT spent,reserved FROM runs WHERE id=?", (run_id,)).fetchone()
            if row["spent"] + row["reserved"] + amount > cap + 1e-10:
                raise ValueError("Spending limit reached. Completed work is saved.")
            request_id = uuid.uuid4().hex
            db.execute("INSERT INTO requests VALUES(?,?,?,NULL,'reserved')", (request_id, run_id, amount))
            db.execute(
                "INSERT INTO request_usage VALUES(?,?)",
                (request_id, json.dumps({**(metadata or {}), "created": time.time()})),
            )
            db.execute("UPDATE runs SET reserved=reserved+? WHERE id=?", (amount, run_id))
            return request_id

    def settle(self, request_id, actual, usage=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
            if row["status"] != "reserved":
                return
            if usage is not None:
                saved = db.execute(
                    "SELECT body FROM request_usage WHERE request_id=?", (request_id,)
                ).fetchone()
                body = {**(json.loads(saved["body"]) if saved else {}), **usage}
                db.execute(
                    "INSERT INTO request_usage VALUES(?,?) ON CONFLICT(request_id) DO UPDATE SET body=excluded.body",
                    (request_id, json.dumps(body)),
                )
            if actual is None:
                return
            db.execute(
                "UPDATE runs SET reserved=MAX(0,reserved-?),spent=spent+? WHERE id=?",
                (row["amount"], actual, row["run_id"]),
            )
            db.execute("UPDATE requests SET actual=?,status='settled' WHERE id=?", (actual, request_id))

    def usage(self, run_id):
        with self.connect() as db:
            db.execute("BEGIN")
            run = self.unpack(db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())
            requests = []
            for row in db.execute(
                "SELECT r.*,u.body FROM requests r LEFT JOIN request_usage u ON u.request_id=r.id "
                "WHERE r.run_id=? ORDER BY r.rowid",
                (run_id,),
            ):
                body = json.loads(row["body"] or "{}")
                requests.append(
                    {
                        **body,
                        "id": row["id"],
                        "status": row["status"],
                        "reserved_usd": None
                        if body.get("provider") == "codex"
                        else row["amount"]
                        if row["status"] == "reserved"
                        else 0,
                        "estimated_cost_usd": None if body.get("provider") == "codex" else row["actual"],
                    }
                )
        fields = ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens")
        totals = {field: sum(r.get(field) or 0 for r in requests) for field in fields}
        codex = run["config"].get("provider") == "codex"
        return {
            "run_id": run_id,
            "currency": "USD",
            "pricing_basis": "codex_subscription" if codex else "standard_uncached_conservative",
            "estimated_cost_usd": None if codex else run["spent"],
            "reserved_usd": None if codex else run["reserved"],
            "budget_usd": None if codex else run["config"].get("budget_usd", 0),
            "request_count": len(requests),
            "unreported_requests": sum(any(r.get(f) is None for f in fields[:2]) for r in requests),
            "reported_counts": {field: sum(r.get(field) is not None for r in requests) for field in fields},
            **totals,
            "total_tokens": totals["input_tokens"] + totals["output_tokens"],
            "requests": requests,
        }

    def layouts(self):
        with self.connect() as db:
            return [self.unpack(r) for r in db.execute(
                "SELECT layouts.*, layout_frames.name AS screenshot_name, "
                "layout_frames.width AS screenshot_width, layout_frames.height AS screenshot_height FROM layouts "
                "LEFT JOIN layout_frames ON layouts.id=layout_frames.layout_id"
            )]

    def layout(self, layout_id):
        with self.connect() as db:
            return self.unpack(db.execute("SELECT * FROM layouts WHERE id=?", (layout_id,)).fetchone())[
                "body"
            ]

    def add_layout(self, body, screenshot=None):
        body = {**body, "name": body["name"].strip()}
        if not body["name"]:
            raise ValueError("Enter a preset name.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            matches = [r["id"] for r in db.execute("SELECT * FROM layouts ORDER BY rowid")
                       if json.loads(r["body"])["name"].strip().casefold() == body["name"].casefold()]
            layout_id = matches[0] if matches else uuid.uuid4().hex
            db.execute(
                "INSERT INTO layouts VALUES(?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                (layout_id, json.dumps(body)),
            )
            if screenshot is not None:
                db.execute(
                    "INSERT INTO layout_frames(layout_id,name,image,width,height) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(layout_id) DO UPDATE SET name=excluded.name, image=excluded.image, "
                    "width=excluded.width, height=excluded.height",
                    (layout_id, *screenshot),
                )
            for duplicate in matches[1:]:
                db.execute("DELETE FROM layouts WHERE id=?", (duplicate,))
        return layout_id

    def layout_frame(self, layout_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM layout_frames WHERE layout_id=?", (layout_id,)).fetchone()
            if row is None:
                raise KeyError(layout_id)
            return row["image"]

    def delete_layout(self, layout_id):
        with self.connect() as db:
            if not db.execute("DELETE FROM layouts WHERE id=?", (layout_id,)).rowcount:
                raise KeyError(layout_id)

    def clips(self, run_id=None):
        with self.connect() as db:
            return [self.unpack(r) for r in db.execute(
                "SELECT clips.*, COALESCE(clip_reviews.status, 'unreviewed') AS review_status, "
                "COALESCE(clip_reviews.note, '') AS review_note "
                "FROM clips JOIN runs ON runs.id=clips.run_id "
                "LEFT JOIN clip_reviews ON clip_reviews.clip_id=clips.id "
                "AND clip_reviews.revision=clips.revision "
                + ("WHERE clips.run_id=? ORDER BY clips.rowid" if run_id is not None
                   else "ORDER BY runs.created DESC, clips.rowid DESC"),
                (run_id,) if run_id is not None else (),
            )]

    def clip(self, clip_id):
        with self.connect() as db:
            return self.unpack(db.execute(
                "SELECT clips.*, COALESCE(clip_reviews.status, 'unreviewed') AS review_status, "
                "COALESCE(clip_reviews.note, '') AS review_note "
                "FROM clips LEFT JOIN clip_reviews ON clip_reviews.clip_id=clips.id "
                "AND clip_reviews.revision=clips.revision WHERE clips.id=?", (clip_id,),
            ).fetchone())

    def review_clip(self, clip_id, expected_revision, status, note=None):
        if status not in {"unreviewed", "approved", "not_approved"}:
            raise ValueError("Choose Unreviewed, Approved or Not approved.")
        if note is not None and (not isinstance(note, str) or len(note) > 2000):
            raise ValueError("Keep the review reason within 2,000 characters.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            clip = self.unpack(db.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone())
            if clip["revision"] != expected_revision:
                raise ValueError("This clip changed. Reload it before reviewing.")
            body = clip["body"]
            if body.get("context_request", {}).get("status") == "pending":
                raise ValueError("Wait for the context review to finish before deciding on this clip.")
            if (body.get("status") not in {"ready", "held"} or not body.get("folder")
                    or not (Path(body["folder"]) / "short.mp4").is_file()):
                raise ValueError("Wait for a rendered preview before reviewing this clip.")
            if note is None:
                previous = db.execute(
                    "SELECT note FROM clip_reviews WHERE clip_id=? AND revision=?",
                    (clip_id, expected_revision),
                ).fetchone()
                note = previous["note"] if previous else ""
            db.execute(
                "INSERT INTO clip_reviews(clip_id,revision,status,note) VALUES(?,?,?,?) "
                "ON CONFLICT(clip_id) DO UPDATE SET "
                "revision=excluded.revision,status=excluded.status,note=excluded.note",
                (clip_id, expected_revision, status, note.strip()),
            )

    def save_clip(self, clip_id, run_id, revision, body):
        with self.connect() as db:
            db.execute(
                "INSERT INTO clips VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "revision=excluded.revision,body=excluded.body",
                (clip_id, run_id, revision, json.dumps(body)),
            )

    def queue_edit(self, clip_id, expected_revision, body):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            clip = self.unpack(db.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone())
            if clip["revision"] != expected_revision:
                raise ValueError("This clip changed. Reload it before saving.")
            if clip["body"].get("context_request", {}).get("status") == "pending":
                raise ValueError("Finish or resume this clip's context review before editing it.")
            active = db.execute(
                "SELECT id FROM runs WHERE id=? AND state IN ('queued','running','paused')", (clip["run_id"],)
            ).fetchone()
            if active:
                raise BusyError(active["id"])
            revision = expected_revision + 1
            db.execute("UPDATE clips SET revision=?,body=? WHERE id=?", (revision, json.dumps(body), clip_id))
            result = json.loads(db.execute("SELECT result FROM runs WHERE id=?", (clip["run_id"],)).fetchone()["result"])
            result["rerender_requested"] = True
            db.execute(
                "UPDATE runs SET state='queued',stage='rerender',intent='',message='',progress=0,result=? WHERE id=?",
                (json.dumps(result), clip["run_id"]),
            )
            return revision

    def queue_context(self, clip_id, request):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            clip = self.unpack(db.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone())
            if clip["revision"] != request["expected_revision"]:
                raise ValueError("This clip changed. Reload it before requesting context.")
            run = self.unpack(db.execute("SELECT * FROM runs WHERE id=?", (clip["run_id"],)).fetchone())
            if run["state"] != "completed":
                raise ValueError("Wait for this recording's current work to finish before requesting context.")
            body = clip["body"]
            if body["status"] != "ready" or not body.get("folder") or not (Path(body["folder"]) / "short.mp4").is_file():
                raise ValueError("Choose a clip with a finished preview to request more context.")
            request = {**request, "id": uuid.uuid4().hex, "status": "pending", "note": request["note"].strip()}
            db.execute("UPDATE clips SET body=? WHERE id=?", (json.dumps({**body, "context_request": request}), clip_id))
            result = {**run["result"], "context_repair_requested": clip_id}
            db.execute(
                "UPDATE runs SET state='queued',stage='context-repair',intent='',message='',progress=0,result=?,updated=? WHERE id=?",
                (json.dumps(result), time.time(), clip["run_id"]),
            )
            return {"run_id": clip["run_id"], "revision": clip["revision"], "request_id": request["id"]}

    def resolve_context(self, clip_id, expected_revision, request_id, reason, revised=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            clip = self.unpack(db.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone())
            request = clip["body"].get("context_request", {})
            if clip["revision"] != expected_revision or request.get("id") != request_id or request.get("status") != "pending":
                raise ValueError("This context request changed. Reload the clip before continuing.")
            body = {**(revised if revised is not None else clip["body"]), "context_request": {
                **request, "status": "expanded" if revised is not None else "unchanged", "reason": reason,
            }}
            revision = expected_revision + int(revised is not None)
            if revised is not None:
                body["previous_revision"] = expected_revision
            db.execute("UPDATE clips SET revision=?,body=? WHERE id=?", (revision, json.dumps(body), clip_id))
            return revision
