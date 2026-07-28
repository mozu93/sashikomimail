import base64

from app.graph import build_payload


def test_build_payload_with_cc_bcc_attachment(tmp_path):
    attachment = tmp_path / "案内.txt"
    attachment.write_text("hello", encoding="utf-8")
    result = build_payload(
        "a@example.jp", "c@example.jp", "b@example.jp", "件名", "本文",
        [str(attachment)],
    )
    message = result["message"]
    assert message["toRecipients"][0]["emailAddress"]["address"] == "a@example.jp"
    assert message["ccRecipients"][0]["emailAddress"]["address"] == "c@example.jp"
    assert base64.b64decode(message["attachments"][0]["contentBytes"]) == b"hello"


def test_build_payload_uses_proxy_sender_when_selected():
    result = build_payload(
        "a@example.jp", "", "", "件名", "本文", [],
        from_address="shared@example.jp",
    )
    assert result["message"]["from"]["emailAddress"]["address"] == "shared@example.jp"


def test_build_payload_omits_sender_for_authenticated_account():
    result = build_payload("a@example.jp", "", "", "件名", "本文", [])
    assert "from" not in result["message"]
