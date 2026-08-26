from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

GITHUB_API_URL = "https://api.github.com/repos/mozu93/sashikomimail/releases/latest"
GITHUB_RELEASES_URL = "https://github.com/mozu93/sashikomimail/releases/latest"
USER_AGENT = "SashikomiMail-updater"


def _version_parts(value: str) -> tuple[int, ...] | None:
    match = re.fullmatch(r"v?(\d+(?:\.\d+)*)", value.strip())
    return tuple(map(int, match.group(1).split("."))) if match else None


def is_newer_version(current: str, latest: str) -> bool:
    current_parts = _version_parts(current)
    latest_parts = _version_parts(latest)
    return bool(current_parts and latest_parts and latest_parts > current_parts)


def check_latest_version(timeout: int = 8) -> dict[str, Any] | None:
    try:
        request = urllib.request.Request(
            GITHUB_API_URL,
            headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        installer = next(
            (
                asset for asset in (data.get("assets") or [])
                if asset.get("name", "").lower().endswith(".exe")
                and "setup" in asset.get("name", "").lower()
            ),
            None,
        )
        checksum = next(
            (asset for asset in (data.get("assets") or [])
             if installer and asset.get("name") == installer.get("name", "") + ".sha256"),
            None,
        )
        tag = str(data.get("tag_name", ""))
        if not tag:
            return None
        return {
            "tag_name": tag,
            "html_url": data.get("html_url") or GITHUB_RELEASES_URL,
            "download_url": installer.get("browser_download_url", "") if checksum else "",
            "checksum_url": checksum.get("browser_download_url", "") if checksum else "",
        }
    except Exception:
        return None


def _is_allowed_download(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname in {
        "github.com", "objects.githubusercontent.com"}


def _parse_sha256_checksum(content: str, filename: str) -> str:
    """リリース添付の ``sha256sum`` 形式を厳密に検証して読み取る。"""
    match = re.fullmatch(
        r"\s*([0-9a-fA-F]{64})\s+\*?([^\r\n]+)\s*", content)
    if not match or Path(match.group(2)).name != filename:
        raise ValueError("更新ファイルのSHA-256チェックサム形式が不正です。")
    return match.group(1).lower()


def _download_checksum(url: str, filename: str) -> str:
    if not _is_allowed_download(url):
        raise ValueError("GitHub以外のチェックサムは利用できません。")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return _parse_sha256_checksum(response.read().decode("ascii"), filename)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(65536):
            digest.update(chunk)
    return digest.hexdigest()


def download_installer(
    url: str,
    checksum_url: str,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    if not _is_allowed_download(url):
        raise ValueError("GitHub以外のダウンロードURLは利用できません。")
    expected_hash = _download_checksum(checksum_url, Path(urlparse(url).path).name)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    fd, raw_path = tempfile.mkstemp(prefix="SashikomiMail-Setup-", suffix=".exe")
    path = Path(raw_path)
    try:
        with urllib.request.urlopen(request, timeout=60) as response, os.fdopen(fd, "wb") as output:
            total = int(response.headers.get("Content-Length", 0))
            received = 0
            while chunk := response.read(65536):
                output.write(chunk)
                received += len(chunk)
                if progress:
                    progress(received, total)
        if path.stat().st_size == 0:
            raise OSError("ダウンロードしたファイルが空です。")
        actual_hash = _sha256_file(path)
        if actual_hash != expected_hash:
            raise ValueError("更新ファイルのSHA-256検証に失敗しました。更新を中止しました。")
        return path
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        path.unlink(missing_ok=True)
        raise


def launch_installer(installer_path: Path) -> None:
    if not getattr(sys, "frozen", False):
        raise RuntimeError("自動更新はインストール済みアプリでのみ利用できます。")
    executable = Path(sys.executable)
    fd, batch_name = tempfile.mkstemp(prefix="SashikomiMail-Updater-", suffix=".bat")
    with os.fdopen(fd, "w", encoding="cp932") as batch:
        batch.write("@echo off\r\n")
        batch.write("timeout /t 3 /nobreak > nul\r\n")
        batch.write(
            f'start "" /wait "{installer_path}" /SILENT /SUPPRESSMSGBOXES '
            "/CLOSEAPPLICATIONS\r\n")
        batch.write(f'start "" "{executable}"\r\n')
        batch.write('del "%~f0"\r\n')
    subprocess.Popen(
        ["cmd", "/c", batch_name],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
