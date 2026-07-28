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


def test_test_send_job_is_persisted_with_type(tmp_path):
    storage = Storage(str(tmp_path / "history.db"))
    job_id = storage.start_job(
        "名簿.xlsx", "【テスト】ご案内", 1, job_type="test")
    storage.add_log(
        job_id, 2, "test@example.jp", "【テスト】ご案内", "成功")
    storage.finish_job(job_id, 1, 0, False)
    job = storage.jobs()[0]
    assert job[8] == "test"
    assert storage.logs(job_id)[0][3] == "成功"


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


def test_pending_and_failed_targets_can_be_retried(tmp_path):
    storage = Storage(str(tmp_path / "retry.db"))
    messages = [
        {"row_number": 2, "to_value": "a@example.jp", "subject": "A",
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
    assert [row[3] for row in logs] == ["エラー", "未送信"]
    assert storage.retry_messages(job_id) == messages


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
