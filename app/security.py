"""Windows DPAPIを使用したローカル保存データの保護。"""

from __future__ import annotations

import base64
import ctypes
import sys
from ctypes import wintypes

_PREFIX = "dpapi:"


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data)
    blob = _DataBlob(
        len(data),
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    return blob, buffer


def protect_text(value: str) -> str:
    if value.startswith(_PREFIX):
        return value
    if sys.platform != "win32":
        raise RuntimeError("保存データの暗号化はWindowsでのみ利用できます。")
    source, source_buffer = _blob(value.encode("utf-8"))
    output = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    if not crypt32.CryptProtectData(
            ctypes.byref(source), "SashikomiMail", None, None, None, 0,
            ctypes.byref(output)):
        raise ctypes.WinError()
    try:
        encrypted = ctypes.string_at(output.pbData, output.cbData)
        return _PREFIX + base64.b64encode(encrypted).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)
        del source_buffer


def unprotect_text(value: str) -> str:
    # 旧バージョンで保存された平文データとの後方互換。
    if not value.startswith(_PREFIX):
        return value
    if sys.platform != "win32":
        raise RuntimeError("保存データの復号はWindowsでのみ利用できます。")
    encrypted = base64.b64decode(value[len(_PREFIX):])
    source, source_buffer = _blob(encrypted)
    output = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    if not crypt32.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None, 0,
            ctypes.byref(output)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)
        del source_buffer
