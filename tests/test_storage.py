import json

from app.security import protect_text
from app.storage import Storage


def test_recipient_list_round_trip_and_update(tmp_path):
    storage = Storage(str(tmp_path / "test.db"))
    storage.save_recipient_list(
        "セミナー名簿", "seminar.xlsx", ["氏名", "メール"],
        [{"氏名": "山田", "メール": "a@example.jp"}],
    )
    saved = storage.recipient_lists()
    assert saved[0]["name"] == "セミナー名簿"
    assert saved[0]["rows"][0]["氏名"] == "山田"

    storage.save_recipient_list(
        "セミナー名簿", "new.xlsx", ["氏名"], [{"氏名": "鈴木"}])
    updated = storage.recipient_lists()
    assert len(updated) == 1
    assert updated[0]["source_name"] == "new.xlsx"
    assert updated[0]["rows"] == [{"氏名": "鈴木"}]

    storage.delete_recipient_list(updated[0]["id"])
    assert storage.recipient_lists() == []


def test_recipient_lists_are_newest_first(tmp_path):
    storage = Storage(str(tmp_path / "test.db"))
    storage.save_recipient_list("先に保存", "first.xlsx", ["メール"], [])
    storage.save_recipient_list("後に保存", "last.xlsx", ["メール"], [])

    assert [item["name"] for item in storage.recipient_lists()] == [
        "後に保存", "先に保存"]


def test_test_send_job_is_persisted_with_type(tmp_path):
    storage = Storage(str(tmp_path / "history.db"))
    job_id = storage.start_job(
        "名簿.xlsx", "【テスト】ご案内", 1, job_type="test")
    storage.add_log(
        job_id, 2, "test@example.jp", "【テスト】ご案内", "成功")
    storage.finish_job(job_id, 1, 0, False)
    job = storage.jobs()[0]
    assert job[8] == "test"
    assert storage.logs(job_id)[0][4] == "成功"


def test_job_file_name_and_subject_are_encrypted_at_rest(tmp_path):
    storage = Storage(str(tmp_path / "history.db"))
    job_id = storage.start_job("人事名簿.xlsx", "個人情報を含む件名", 1)
    with storage.connect() as db:
        raw = db.execute(
            "SELECT file_name,subject FROM send_jobs WHERE id=?", (job_id,)).fetchone()
    assert all(value.startswith("dpapi:") for value in raw)
    assert storage.jobs()[0][2:4] == ("人事名簿.xlsx", "個人情報を含む件名")


def test_template_round_trip_update_by_id_and_delete(tmp_path):
    storage = Storage(str(tmp_path / "template.db"))
    storage.save_template("案内", "件名A", "本文A")
    template = storage.templates()[0]
    assert template["subject"] == "件名A"
    storage.save_template("案内", "件名B", "本文B")
    assert storage.templates()[0]["subject"] == "件名B"
    storage.save_template("案内2", "件名C", "本文C", template["id"])
    assert storage.templates()[0]["name"] == "案内2"
    assert storage.templates()[0]["subject"] == "件名C"
    storage.delete_template(template["id"])
    assert storage.templates() == []


def test_signature_round_trip_update_and_delete(tmp_path):
    storage = Storage(str(tmp_path / "signature.db"))
    storage.save_signature("総務", "○○商工会議所\n総務部")
    signature = storage.signatures()[0]
    assert signature["body"] == "○○商工会議所\n総務部"
    storage.save_signature("総務", "更新後")
    assert storage.signatures()[0]["body"] == "更新後"
    storage.save_signature("総務部", "名称変更", signature["id"])
    assert storage.signatures()[0]["name"] == "総務部"
    storage.delete_signature(signature["id"])
    assert storage.signatures() == []


def test_cc_contact_round_trip_update_delete_and_encrypted(tmp_path):
    storage = Storage(str(tmp_path / "cc_contacts.db"))
    storage.save_cc_contact("山田部長", "yamada@example.jp")
    contact = storage.cc_contacts()[0]
    assert contact["email"] == "yamada@example.jp"
    storage.save_cc_contact("山田部長", "yamada2@example.jp")
    assert storage.cc_contacts()[0]["email"] == "yamada2@example.jp"
    storage.save_cc_contact("山田本部長", "yamada2@example.jp", contact["id"])
    assert storage.cc_contacts()[0]["name"] == "山田本部長"
    with storage.connect() as db:
        raw = db.execute("SELECT email FROM cc_contacts").fetchone()[0]
    assert raw.startswith("dpapi:")
    assert "yamada2@example.jp" not in raw
    storage.delete_cc_contact(contact["id"])
    assert storage.cc_contacts() == []


def test_pending_and_failed_targets_can_be_retried(tmp_path):
    storage = Storage(str(tmp_path / "retry.db"))
    messages = [
        {"row_number": 2, "organization_name": "青空事業所", "to_value": "a@example.jp", "subject": "A",
         "body": "本文", "cc_value": "", "bcc_value": "",
         "attachment_paths": []},
        {"row_number": 3, "to_value": "b@example.jp", "subject": "B",
         "body": "本文", "cc_value": "", "bcc_value": "",
         "attachment_paths": []},
    ]
    job_id = storage.start_job(
        "名簿.xlsx", "案内", 2, messages=messages)
    storage.add_log(job_id, 2, "a@example.jp", "A", "エラー", "送信失敗")
    logs = storage.logs(job_id)
    assert [row[4] for row in logs] == ["エラー", "未送信"]
    assert logs[0][1] == "青空事業所"
    assert storage.target_message(job_id, 2) == messages[0]
    assert storage.retry_messages(job_id) == messages


def test_partially_delivered_target_is_not_retried(tmp_path):
    message = {"row_number": 2, "to_value": "a@example.jp", "subject": "A",
               "body": "本文", "cc_value": "", "bcc_value": "", "attachment_paths": []}
    storage = Storage(str(tmp_path / "partial.db"))
    job_id = storage.start_job("名簿.xlsx", "案内", 1, messages=[message], provider="gmail")
    storage.add_log(job_id, 2, "a@example.jp", "A", "一部送信", "Gmailに拒否された宛先があります。")
    assert storage.logs(job_id)[0][4] == "一部送信"
    assert storage.retry_messages(job_id) == []


def test_sensitive_recipient_data_is_not_plaintext_in_database(tmp_path):
    storage = Storage(str(tmp_path / "encrypted.db"))
    storage.save_recipient_list(
        "名簿", "data.xlsx", ["氏名"],
        [{"氏名": "非常に固有な暗号化確認用氏名"}])
    with storage.connect() as db:
        raw = db.execute(
            "SELECT rows_json FROM recipient_lists").fetchone()[0]
    assert raw.startswith("dpapi:")
    assert "非常に固有な暗号化確認用氏名" not in raw


def test_sensitive_settings_are_encrypted_and_readable(tmp_path):
    storage = Storage(str(tmp_path / "settings.db"))
    storage.save_settings({
        "test_address": "secret@example.jp",
        "account_username": "user@example.jp",
    })
    with storage.connect() as db:
        raw = dict(db.execute(
            "SELECT key,value FROM settings").fetchall())
    assert "secret@example.jp" not in raw["test_address"]
    assert storage.settings()["test_address"] == "secret@example.jp"


def test_encrypted_setting_is_readable_even_if_key_list_lacks_it(tmp_path):
    """保護対象キーの一覧に無くても、暗号化済みの値は復号して読める。

    新しい版が暗号化して保存したキーを古い版が平文とみなし、起動時に
    JSONDecodeErrorで落ちた不具合の回帰確認。
    """
    storage = Storage(str(tmp_path / "forward.db"))
    protected = protect_text(json.dumps("user@gmail.com", ensure_ascii=False))
    with storage.connect() as db:
        db.execute(
            "INSERT INTO settings(key,value) VALUES('future_key',?)", (protected,))
    assert "future_key" not in Storage._PROTECTED_SETTINGS_KEYS
    assert storage.settings()["future_key"] == "user@gmail.com"


def test_unreadable_setting_is_skipped_instead_of_raising(tmp_path):
    """壊れた設定値が1件あってもアプリを起動不能にしない。"""
    storage = Storage(str(tmp_path / "broken.db"))
    storage.save_settings({"tenant_id": "abc"})
    with storage.connect() as db:
        db.execute(
            "INSERT INTO settings(key,value) VALUES('interval_ms','')")
    settings = storage.settings()
    assert settings["tenant_id"] == "abc"
    assert "interval_ms" not in settings


def test_retired_delivery_trace_credentials_are_removed(tmp_path):
    path = tmp_path / "retired-settings.db"
    storage = Storage(str(path))
    storage.save_settings({"trace_client_secret": "secret", "trace_sender_address": "a@example.jp"})

    reopened = Storage(str(path))
    assert "trace_client_secret" not in reopened.settings()
    assert "trace_sender_address" not in reopened.settings()
