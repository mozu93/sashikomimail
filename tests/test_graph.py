import base64

import pytest
import requests

from app.graph import SendCancelled, build_payload, send_mail


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


class FakeResponse:
    def __init__(self, status_code, text="", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


def test_graph_error_does_not_expose_response_body(monkeypatch):
    monkeypatch.setattr(
        "app.graph.requests.post",
        lambda *args, **kwargs: FakeResponse(400, "recipient@example.jp and bearer-secret"),
    )

    with pytest.raises(RuntimeError) as raised:
        send_mail({}, "token", to_value="a@example.jp", cc_value="", bcc_value="",
                  subject="件名", body="本文", attachment_paths=[])

    assert "recipient@example.jp" not in str(raised.value)
    assert "bearer-secret" not in str(raised.value)
    assert "送信要求" in str(raised.value)


def test_graph_connection_error_is_sanitized(monkeypatch):
    def fail(*args, **kwargs):
        raise requests.ConnectionError("https://example.invalid/?token=secret")

    monkeypatch.setattr("app.graph.requests.post", fail)
    with pytest.raises(RuntimeError) as raised:
        send_mail({}, "token", to_value="a@example.jp", cc_value="", bcc_value="",
                  subject="件名", body="本文", attachment_paths=[])
    assert "secret" not in str(raised.value)
    assert "接続できません" in str(raised.value)


def test_graph_throttle_wait_can_be_cancelled_without_retry(monkeypatch):
    calls = []

    def throttled(*args, **kwargs):
        calls.append(1)
        return FakeResponse(429, headers={"Retry-After": "60"})

    monkeypatch.setattr("app.graph.requests.post", throttled)
    with pytest.raises(SendCancelled):
        send_mail({}, "token", is_cancelled=lambda: True,
                  to_value="a@example.jp", cc_value="", bcc_value="",
                  subject="件名", body="本文", attachment_paths=[])
    assert len(calls) == 0


def test_graph_throttle_cancelled_during_wait_does_not_retry(monkeypatch):
    calls = []
    cancelled = False

    def throttled(*args, **kwargs):
        nonlocal cancelled
        calls.append(1)
        cancelled = True
        return FakeResponse(429, headers={"Retry-After": "60"})

    monkeypatch.setattr("app.graph.requests.post", throttled)
    with pytest.raises(SendCancelled):
        send_mail({}, "token", is_cancelled=lambda: cancelled,
                  to_value="a@example.jp", cc_value="", bcc_value="",
                  subject="件名", body="本文", attachment_paths=[])
    assert len(calls) == 1
