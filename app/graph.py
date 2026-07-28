from __future__ import annotations

import base64
import mimetypes
import time
from pathlib import Path

import msal
import requests
from msal_extensions import PersistedTokenCache, build_encrypted_persistence

from app.core import split_addresses
from app.storage import data_dir

# GraphのJSON直接送信ではBase64化で約4/3に増えるため、
# 3MB未満のAPI境界に余裕を持たせて生ファイル合計を2.5MBに制限する。
ATTACHMENT_LIMIT = int(2.5 * 1024 * 1024)


def _public_client(config: dict):
    if not config.get("tenant_id") or not config.get("client_id"):
        raise ValueError("設定画面でテナントIDとクライアントIDを入力してください。")
    persistence = build_encrypted_persistence(str(data_dir() / "token_cache.bin"))
    cache = PersistedTokenCache(persistence)
    return msal.PublicClientApplication(
        config["client_id"],
        authority=f"https://login.microsoftonline.com/{config['tenant_id']}",
        token_cache=cache,
    )


def get_cached_accounts(config: dict) -> list[str]:
    app = _public_client(config)
    return sorted({
        account.get("username", "")
        for account in app.get_accounts()
        if account.get("username")
    })


def get_access_token(config: dict, force_interactive: bool = False) -> tuple[str, str]:
    app = _public_client(config)
    scopes = ["https://graph.microsoft.com/Mail.Send"]
    if config.get("from_address", "").strip():
        scopes.append("https://graph.microsoft.com/Mail.Send.Shared")
    accounts = app.get_accounts()
    selected_username = config.get("account_username", "").casefold()
    selected = next(
        (account for account in accounts
         if account.get("username", "").casefold() == selected_username),
        None,
    )
    if not selected and len(accounts) == 1:
        selected = accounts[0]
    if not selected and len(accounts) > 1 and not force_interactive:
        raise RuntimeError("設定画面で送信に使用するMicrosoft 365アカウントを選択してください。")
    result = (
        app.acquire_token_silent(scopes, account=selected)
        if selected and not force_interactive else None
    )
    if not result:
        result = app.acquire_token_interactive(scopes=scopes)
    if "access_token" not in result:
        raise RuntimeError(result.get("error_description", "Microsoft 365認証に失敗しました。"))
    username = (
        result.get("id_token_claims", {}).get("preferred_username")
        or (selected or {}).get("username")
        or ""
    )
    return result["access_token"], username


def sign_out(config: dict) -> None:
    app = _public_client(config)
    for account in app.get_accounts():
        app.remove_account(account)


def build_payload(to_value: str, cc_value: str, bcc_value: str, subject: str,
                  body: str, attachment_paths: list[str], from_address: str = "") -> dict:
    attachments = []
    total = sum(Path(path).stat().st_size for path in attachment_paths)
    if total > ATTACHMENT_LIMIT:
        raise ValueError("添付ファイル合計が安全上限の2.5MBを超えています。")
    for value in attachment_paths:
        path = Path(value)
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        attachments.append({
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": path.name,
            "contentType": media_type,
            "contentBytes": base64.b64encode(path.read_bytes()).decode("ascii"),
        })
    recipient = lambda address: {"emailAddress": {"address": address}}
    message = {
        "subject": subject,
        "body": {"contentType": "Text", "content": body},
        "toRecipients": [recipient(x) for x in split_addresses(to_value)],
        "ccRecipients": [recipient(x) for x in split_addresses(cc_value)],
        "bccRecipients": [recipient(x) for x in split_addresses(bcc_value)],
        "attachments": attachments,
    }
    if from_address:
        message["from"] = recipient(from_address)
    return {"message": message, "saveToSentItems": True}


def send_mail(config: dict, token: str, **message) -> None:
    payload = build_payload(from_address=config.get("from_address", ""), **message)
    for attempt in range(4):
        response = requests.post(
            "https://graph.microsoft.com/v1.0/me/sendMail",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload, timeout=30,
        )
        if response.status_code in (200, 202):
            return
        if response.status_code == 429 and attempt < 3:
            try:
                delay = min(int(response.headers.get("Retry-After", "5")), 60)
            except ValueError:
                delay = 5
            time.sleep(delay)
            continue
        raise RuntimeError(f"送信失敗 ({response.status_code}): {response.text[:300]}")
