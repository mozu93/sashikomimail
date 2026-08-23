from __future__ import annotations

import mimetypes
import smtplib
from email.message import EmailMessage
from pathlib import Path

from app.core import split_addresses

# Gmail送信の実用上限（約25MB）。Graph APIの2.5MB制限とは別に扱う。
GMAIL_ATTACHMENT_LIMIT = 25 * 1024 * 1024

_GMAIL_SMTP_HOST = "smtp.gmail.com"
_GMAIL_SMTP_PORT = 465


def build_gmail_message(from_address: str, to_value: str, cc_value: str, bcc_value: str,
                        subject: str, body: str, attachment_paths: list[str]) -> EmailMessage:
    message = EmailMessage()
    message["From"] = from_address
    message["To"] = ", ".join(split_addresses(to_value))
    if cc_value:
        message["Cc"] = ", ".join(split_addresses(cc_value))
    message["Subject"] = subject
    message.set_content(body)
    for value in attachment_paths:
        path = Path(value)
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        maintype, _, subtype = media_type.partition("/")
        message.add_attachment(
            path.read_bytes(), maintype=maintype, subtype=subtype or "octet-stream",
            filename=path.name)
    return message


def open_gmail_connection(gmail_config: dict) -> smtplib.SMTP_SSL:
    """Gmail SMTPへログイン済みの接続を1つ開く（複数通の送信で使い回せる）。"""
    address = gmail_config.get("gmail_address", "").strip()
    app_password = gmail_config.get("gmail_app_password", "")
    if not address or not app_password:
        raise ValueError("Gmailアドレスとアプリパスワードを設定してください。")
    conn = smtplib.SMTP_SSL(_GMAIL_SMTP_HOST, _GMAIL_SMTP_PORT, timeout=30)
    conn.login(address, app_password)
    return conn


def sanitize_smtp_error(error: Exception) -> str:
    """履歴に個人情報や資格情報を含むSMTPエラー詳細を保存しない。"""
    if isinstance(error, smtplib.SMTPAuthenticationError):
        return "Gmailの認証に失敗しました。アプリパスワードを確認してください。"
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        return "宛先アドレスがGmailに拒否されました。"
    if isinstance(error, smtplib.SMTPSenderRefused):
        return "差出人アドレスがGmailに拒否されました。"
    if isinstance(error, (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected)):
        return "Gmailサーバーへの接続に失敗しました。"
    return "Gmail送信でエラーが発生しました。"


def send_mail_gmail(gmail_config: dict, connection: smtplib.SMTP_SSL, to_value: str,
                    cc_value: str, bcc_value: str, subject: str, body: str,
                    attachment_paths: list[str]) -> None:
    address = gmail_config.get("gmail_address", "").strip()
    message = build_gmail_message(
        address, to_value, cc_value, bcc_value, subject, body, attachment_paths)
    recipients = (
        split_addresses(to_value) + split_addresses(cc_value) + split_addresses(bcc_value)
    )
    try:
        connection.send_message(message, from_addr=address, to_addrs=recipients)
    except smtplib.SMTPException as exc:
        raise RuntimeError(sanitize_smtp_error(exc)) from exc
