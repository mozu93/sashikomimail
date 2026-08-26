from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from app.security import protect_text, unprotect_text

# 復号・JSON解析に失敗した設定値の目印。Noneを保存した設定と区別する。
_UNREADABLE = object()


def data_dir() -> Path:
    root = Path(os.environ.get("APPDATA", Path.home())) / "SashikomiMail"
    root.mkdir(parents=True, exist_ok=True)
    return root


class Storage:
    def __init__(self, path: str | None = None):
        self.path = path or str(data_dir() / "sashikomi_mail.db")
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS templates(
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
                    subject TEXT NOT NULL, body TEXT NOT NULL,
                    updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS send_jobs(
                    id INTEGER PRIMARY KEY, created_at TEXT NOT NULL,
                    file_name TEXT NOT NULL, subject TEXT NOT NULL,
                    total INTEGER NOT NULL, success INTEGER NOT NULL,
                    error INTEGER NOT NULL, cancelled INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS send_logs(
                    id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL,
                    row_number INTEGER NOT NULL, to_address TEXT NOT NULL,
                    subject TEXT NOT NULL, status TEXT NOT NULL,
                    error_message TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(job_id) REFERENCES send_jobs(id));
                CREATE TABLE IF NOT EXISTS settings(
                    key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS recipient_lists(
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
                    source_name TEXT NOT NULL, headers_json TEXT NOT NULL,
                    rows_json TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS signatures(
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
                    body TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS cc_contacts(
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
                    email TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS send_targets(
                    id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL,
                    row_number INTEGER NOT NULL, to_address TEXT NOT NULL,
                    subject TEXT NOT NULL, payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    error_message TEXT NOT NULL DEFAULT '',
                    provider TEXT NOT NULL DEFAULT 'm365');
            """)
            columns = {
                row[1] for row in db.execute("PRAGMA table_info(send_jobs)").fetchall()
            }
            if "job_type" not in columns:
                db.execute(
                    "ALTER TABLE send_jobs ADD COLUMN job_type TEXT NOT NULL DEFAULT 'bulk'")
            target_columns = {
                row[1] for row in db.execute("PRAGMA table_info(send_targets)").fetchall()
            }
            if "provider" not in target_columns:
                db.execute(
                    "ALTER TABLE send_targets ADD COLUMN provider TEXT NOT NULL DEFAULT 'm365'")
            # 配信追跡機能を廃止したため、過去に入力された追跡用資格情報も残さない。
            db.execute("DELETE FROM settings WHERE key IN (?, ?)",
                       ("trace_client_secret", "trace_sender_address"))
            self._migrate_sensitive_data(db)

    @staticmethod
    def _migrate_sensitive_data(db: sqlite3.Connection) -> None:
        for row_id, value in db.execute(
                "SELECT id,rows_json FROM recipient_lists").fetchall():
            if not value.startswith("dpapi:"):
                db.execute(
                    "UPDATE recipient_lists SET rows_json=? WHERE id=?",
                    (protect_text(value), row_id))
        for key, value in db.execute(
                "SELECT key,value FROM settings WHERE key IN "
                "('from_address','test_address','account_username')").fetchall():
            if not value.startswith("dpapi:"):
                db.execute(
                    "UPDATE settings SET value=? WHERE key=?",
                    (protect_text(value), key))
        for row_id, to_address, subject, error in db.execute(
                "SELECT id,to_address,subject,error_message FROM send_logs").fetchall():
            db.execute("""UPDATE send_logs SET to_address=?,subject=?,error_message=?
                WHERE id=?""",
                (protect_text(to_address), protect_text(subject),
                 protect_text(error), row_id))
        for row_id, file_name, subject in db.execute(
                "SELECT id,file_name,subject FROM send_jobs").fetchall():
            if not file_name.startswith("dpapi:") or not subject.startswith("dpapi:"):
                db.execute("UPDATE send_jobs SET file_name=?,subject=? WHERE id=?",
                           (protect_text(file_name), protect_text(subject), row_id))

    def connect(self):
        return sqlite3.connect(self.path)

    def templates(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT id,name,subject,body,updated_at FROM templates ORDER BY name"
            ).fetchall()
        return [dict(zip(("id", "name", "subject", "body", "updated_at"), row)) for row in rows]

    def save_template(self, name: str, subject: str, body: str,
                      template_id: int | None = None) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as db:
            if template_id is not None:
                db.execute(
                    "UPDATE templates SET name=?,subject=?,body=?,updated_at=? WHERE id=?",
                    (name, subject, body, now, template_id))
                return
            db.execute("""INSERT INTO templates(name,subject,body,updated_at)
                VALUES(?,?,?,?) ON CONFLICT(name) DO UPDATE SET
                subject=excluded.subject,body=excluded.body,updated_at=excluded.updated_at""",
                       (name, subject, body, now))

    def delete_template(self, template_id: int) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM templates WHERE id=?", (template_id,))

    def recipient_lists(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("""SELECT id,name,source_name,headers_json,rows_json,updated_at
                FROM recipient_lists ORDER BY updated_at DESC, id DESC""").fetchall()
        return [{
            "id": row[0], "name": row[1], "source_name": row[2],
            "headers": json.loads(row[3]),
            "rows": json.loads(unprotect_text(row[4])),
            "updated_at": row[5],
        } for row in rows]

    def save_recipient_list(self, name: str, source_name: str,
                            headers: list[str], rows: list[dict[str, str]]) -> None:
        # 同一秒内の連続保存でも、最新の名簿を先頭へ確実に表示する。
        now = datetime.now().isoformat(timespec="microseconds")
        with self.connect() as db:
            db.execute("""INSERT INTO recipient_lists
                (name,source_name,headers_json,rows_json,updated_at)
                VALUES(?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET
                source_name=excluded.source_name,headers_json=excluded.headers_json,
                rows_json=excluded.rows_json,updated_at=excluded.updated_at""",
                (name, source_name, json.dumps(headers, ensure_ascii=False),
                 protect_text(json.dumps(rows, ensure_ascii=False)), now))

    def delete_recipient_list(self, list_id: int) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM recipient_lists WHERE id=?", (list_id,))

    def signatures(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT id,name,body,updated_at FROM signatures ORDER BY name"
            ).fetchall()
        return [
            dict(zip(("id", "name", "body", "updated_at"), row))
            for row in rows
        ]

    def save_signature(self, name: str, body: str,
                       signature_id: int | None = None) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as db:
            if signature_id is not None:
                db.execute(
                    "UPDATE signatures SET name=?,body=?,updated_at=? WHERE id=?",
                    (name, body, now, signature_id))
                return
            db.execute("""INSERT INTO signatures(name,body,updated_at)
                VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET
                body=excluded.body,updated_at=excluded.updated_at""",
                (name, body, now))

    def delete_signature(self, signature_id: int) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM signatures WHERE id=?", (signature_id,))

    def cc_contacts(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT id,name,email,updated_at FROM cc_contacts ORDER BY name"
            ).fetchall()
        return [
            {"id": row[0], "name": row[1], "email": unprotect_text(row[2]),
             "updated_at": row[3]}
            for row in rows
        ]

    def save_cc_contact(self, name: str, email: str,
                        contact_id: int | None = None) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        protected_email = protect_text(email)
        with self.connect() as db:
            if contact_id is not None:
                db.execute(
                    "UPDATE cc_contacts SET name=?,email=?,updated_at=? WHERE id=?",
                    (name, protected_email, now, contact_id))
                return
            db.execute("""INSERT INTO cc_contacts(name,email,updated_at)
                VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET
                email=excluded.email,updated_at=excluded.updated_at""",
                (name, protected_email, now))

    def delete_cc_contact(self, contact_id: int) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM cc_contacts WHERE id=?", (contact_id,))

    _PROTECTED_SETTINGS_KEYS = {
        "from_address", "test_address", "account_username",
        "gmail_address", "gmail_app_password", "gmail_test_address",
    }

    @staticmethod
    def _decode_setting(key: str, value: str):
        """保存済みの設定値を1件復号する。読めない値は_UNREADABLEを返す。

        復号は保護対象キーの一覧ではなく`dpapi:`の有無で判断する。保護対象
        キーは版を追うごとに増えるため（例：Gmail関連キーはv1.3.0で追加）、
        一覧に無いキーを平文とみなすと、暗号化済みの値をそのまま
        `json.loads`へ渡して起動時に落ちる。
        値が1件壊れていてもアプリごと起動不能にはせず、その設定だけを
        未設定として扱う。
        """
        try:
            return json.loads(unprotect_text(value))
        except Exception:
            logging.getLogger(__name__).warning(
                "設定 %s を読み込めなかったため未設定として扱います。", key)
            return _UNREADABLE

    def settings(self) -> dict:
        with self.connect() as db:
            rows = db.execute("SELECT key,value FROM settings").fetchall()
        decoded = {key: self._decode_setting(key, value) for key, value in rows}
        return {
            key: value for key, value in decoded.items() if value is not _UNREADABLE
        }

    def save_settings(self, values: dict) -> None:
        with self.connect() as db:
            for key, value in values.items():
                serialized = json.dumps(value, ensure_ascii=False)
                if key in self._PROTECTED_SETTINGS_KEYS:
                    serialized = protect_text(serialized)
                db.execute("""INSERT INTO settings(key,value) VALUES(?,?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                           (key, serialized))

    def start_job(self, file_name: str, subject: str, total: int,
                  job_type: str = "bulk",
                  messages: list[dict] | None = None,
                  provider: str = "m365") -> int:
        with self.connect() as db:
            cur = db.execute("""INSERT INTO send_jobs
                (created_at,file_name,subject,total,success,error,cancelled,job_type)
                VALUES(?,?,?,?,0,0,0,?)""",
                (datetime.now().isoformat(timespec="seconds"),
                 protect_text(file_name), protect_text(subject), total, job_type))
            job_id = int(cur.lastrowid)
            for message in messages or []:
                db.execute("""INSERT INTO send_targets
                    (job_id,row_number,to_address,subject,payload_json,status,error_message,provider)
                    VALUES(?,?,?,?,?,'pending','',?)""",
                    (job_id, message["row_number"],
                     protect_text(message["to_value"]),
                     protect_text(message["subject"]),
                     protect_text(json.dumps(message, ensure_ascii=False)), provider))
            return job_id

    def add_log(self, job_id: int, row_number: int, to_address: str,
                subject: str, status: str, error: str = "") -> None:
        with self.connect() as db:
            target = db.execute(
                "SELECT id FROM send_targets WHERE job_id=? AND row_number=?",
                (job_id, row_number)).fetchone()
            if target:
                target_status = {"成功": "success", "一部送信": "partial"}.get(status, "error")
                db.execute("""UPDATE send_targets SET status=?,error_message=?
                    WHERE id=?""",
                    (target_status,
                     protect_text(error), target[0]))
                return
            db.execute("""INSERT INTO send_logs
                (job_id,row_number,to_address,subject,status,error_message)
                VALUES(?,?,?,?,?,?)""",
                (job_id, row_number, protect_text(to_address),
                 protect_text(subject), status, protect_text(error)))

    def finish_job(self, job_id: int, success: int, error: int, cancelled: bool) -> None:
        with self.connect() as db:
            db.execute("UPDATE send_jobs SET success=?,error=?,cancelled=? WHERE id=?",
                       (success, error, int(cancelled), job_id))

    def jobs(self) -> list[tuple]:
        with self.connect() as db:
            rows = db.execute("""SELECT id,created_at,file_name,subject,total,success,error,
                cancelled,job_type
                FROM send_jobs ORDER BY id DESC""").fetchall()
        return [
            (row[0], row[1], unprotect_text(row[2]), unprotect_text(row[3]), *row[4:])
            for row in rows
        ]

    def logs(self, job_id: int) -> list[tuple]:
        with self.connect() as db:
            targets = db.execute("""SELECT row_number,to_address,subject,payload_json,status,error_message,
                provider
                FROM send_targets WHERE job_id=? ORDER BY id""", (job_id,)).fetchall()
            if targets:
                label = {"success": "成功", "error": "エラー", "pending": "未送信",
                         "partial": "一部送信"}
                return [
                    (row[0], json.loads(unprotect_text(row[3])).get("organization_name", ""),
                     unprotect_text(row[1]), unprotect_text(row[2]),
                     label.get(row[4], row[4]), unprotect_text(row[5]), row[6])
                    for row in targets
                ]
            rows = db.execute("""SELECT row_number,to_address,subject,status,error_message
                FROM send_logs WHERE job_id=? ORDER BY id""", (job_id,)).fetchall()
        return [
            (row[0], "", unprotect_text(row[1]), unprotect_text(row[2]), row[3],
             unprotect_text(row[4]), "")
            for row in rows
        ]

    def delete_job(self, job_id: int) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM send_targets WHERE job_id=?", (job_id,))
            db.execute("DELETE FROM send_logs WHERE job_id=?", (job_id,))
            db.execute("DELETE FROM send_jobs WHERE id=?", (job_id,))

    def delete_old_jobs(self, retention_days: int) -> int:
        cutoff = (datetime.now() - timedelta(days=retention_days)).isoformat(
            timespec="seconds")
        with self.connect() as db:
            ids = [
                row[0] for row in db.execute(
                    "SELECT id FROM send_jobs WHERE created_at < ?", (cutoff,)
                ).fetchall()
            ]
            for job_id in ids:
                db.execute("DELETE FROM send_targets WHERE job_id=?", (job_id,))
                db.execute("DELETE FROM send_logs WHERE job_id=?", (job_id,))
                db.execute("DELETE FROM send_jobs WHERE id=?", (job_id,))
        return len(ids)

    def retry_messages(self, job_id: int) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("""SELECT payload_json FROM send_targets
                WHERE job_id=? AND status IN ('error','pending') ORDER BY id""",
                (job_id,)).fetchall()
        return [
            json.loads(unprotect_text(row[0]))
            for row in rows
        ]

    def target_message(self, job_id: int, row_number: int) -> dict | None:
        """履歴明細の送信時点のメール内容を返す。旧履歴では取得できない。"""
        with self.connect() as db:
            row = db.execute("""SELECT payload_json FROM send_targets
                WHERE job_id=? AND row_number=?""", (job_id, row_number)).fetchone()
        return json.loads(unprotect_text(row[0])) if row else None
