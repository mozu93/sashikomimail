import pytest

from app.gmail_smtp import build_gmail_message, open_gmail_connection


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
