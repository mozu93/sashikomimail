import pytest

from app.gmail_smtp import build_gmail_message, open_gmail_connection, send_mail_gmail
from app.graph import SendCancelled
from app.ui import SendWorker


def test_build_gmail_message_with_cc_and_attachment(tmp_path):
    attachment = tmp_path / "案内.txt"
    attachment.write_text("hello", encoding="utf-8")
    message = build_gmail_message(
        "from@example.jp", "a@example.jp", "c@example.jp", "b@example.jp",
        "件名", "本文", [str(attachment)],
    )
    assert message["From"] == "from@example.jp"
    assert message["To"] == "a@example.jp"
    assert message["Cc"] == "c@example.jp"
    assert "Bcc" not in message
    assert message.get_body(preferencelist=("plain",)).get_content().strip() == "本文"
    attachments = list(message.iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "案内.txt"
    assert attachments[0].get_payload(decode=True) == b"hello"


def test_build_gmail_message_without_cc_omits_header():
    message = build_gmail_message(
        "from@example.jp", "a@example.jp", "", "", "件名", "本文", [],
    )
    assert "Cc" not in message


def test_open_gmail_connection_requires_address_and_password():
    with pytest.raises(ValueError):
        open_gmail_connection({"gmail_address": "", "gmail_app_password": ""})


def test_send_mail_returns_refused_recipients():
    class FakeConnection:
        def send_message(self, _message, **_kwargs):
            return {"rejected@example.jp": (550, b"Rejected")}

    refused = send_mail_gmail(
        {"gmail_address": "from@example.jp"}, FakeConnection(), "to@example.jp", "", "",
        "件名", "本文", [])
    assert refused == {"rejected@example.jp": (550, b"Rejected")}


def test_send_worker_interval_returns_immediately_after_cancellation():
    worker = SendWorker({}, [], 60_000)
    worker.cancel()
    assert worker.wait_interval() is False


def test_send_worker_records_graph_cancellation_without_error(monkeypatch):
    message = {
        "row_number": 2, "to_value": "to@example.jp", "cc_value": "", "bcc_value": "",
        "subject": "件名", "body": "本文", "attachment_paths": [],
    }
    monkeypatch.setattr("app.ui.get_access_token", lambda _config: ("token", ""))
    monkeypatch.setattr(
        "app.ui.send_mail", lambda *_args, **_kwargs: (_ for _ in ()).throw(SendCancelled()))
    worker = SendWorker({"provider": "m365"}, [message], 0)
    completed = []
    logged = []
    worker.completed.connect(lambda *result: completed.append(result))
    worker.logged.connect(lambda *result: logged.append(result))

    worker.run()

    assert completed == [(0, 0, True)]
    assert logged == []
