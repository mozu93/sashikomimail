from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from openpyxl import load_workbook

EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")
TAG_RE = re.compile(r"\{([^{}]+)\}")
CONDITIONAL_TAG_RE = re.compile(r"\{([^{}|]+)\|([^{}]*)\}")


@dataclass
class ImportResult:
    headers: list[str]
    rows: list[dict[str, str]]
    warnings: list[str]


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _unique_headers(values: Iterable[object]) -> tuple[list[str], list[str]]:
    headers, warnings, seen = [], [], {}
    for index, value in enumerate(values, 1):
        base = _text(value) or f"列{index}"
        seen[base] = seen.get(base, 0) + 1
        name = base if seen[base] == 1 else f"{base}_{seen[base]}"
        if name != base:
            warnings.append(f"重複ヘッダー「{base}」を「{name}」に変更しました。")
        headers.append(name)
    return headers, warnings


def load_recipient_file(path: str) -> ImportResult:
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".csv":
        raw = None
        for encoding in ("utf-8-sig", "cp932", "utf-8"):
            try:
                with source.open(encoding=encoding, newline="") as handle:
                    raw = list(csv.reader(handle))
                break
            except UnicodeDecodeError:
                continue
        if raw is None:
            raise ValueError("CSVの文字コードを判定できませんでした。")
    elif suffix == ".xlsx":
        book = load_workbook(source, read_only=True, data_only=True)
        sheet = book.active
        raw = [list(row) for row in sheet.iter_rows(values_only=True)]
        book.close()
    elif suffix == ".xls":
        try:
            import xlrd
        except ImportError as exc:
            raise RuntimeError("xlsの読込には xlrd が必要です。") from exc
        sheet = xlrd.open_workbook(source).sheet_by_index(0)
        raw = [sheet.row_values(i) for i in range(sheet.nrows)]
    else:
        raise ValueError("対応形式は xlsx、xls、csv です。")
    if not raw:
        raise ValueError("ファイルにデータがありません。")
    headers, warnings = _unique_headers(raw[0])
    rows = []
    for values in raw[1:]:
        values = list(values) + [""] * (len(headers) - len(values))
        row = {header: _text(values[i]) for i, header in enumerate(headers)}
        if any(row.values()):
            rows.append(row)
    if not rows:
        warnings.append("データ行がありません。")
    return ImportResult(headers, rows, warnings)


def split_addresses(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,;\n]", value or "") if item.strip()]


def is_valid_email(value: str) -> bool:
    return bool(EMAIL_RE.fullmatch(value.strip()))


def validate_rows(rows: list[dict[str, str]], to_column: str,
                  cc_column: str = "") -> dict[int, list[str]]:
    errors: dict[int, list[str]] = {}
    seen: dict[str, int] = {}
    for index, row in enumerate(rows):
        issues = []
        recipients = split_addresses(row.get(to_column, ""))
        if not recipients:
            issues.append("宛先が空です")
        elif any(not is_valid_email(address) for address in recipients):
            issues.append("宛先の形式が不正です")
        for address in recipients:
            key = address.casefold()
            if key in seen:
                issues.append(f"宛先が{seen[key] + 2}行目と重複しています")
            else:
                seen[key] = index
        if cc_column:
            cc = split_addresses(row.get(cc_column, ""))
            if any(not is_valid_email(address) for address in cc):
                issues.append("CCの形式が不正です")
        if issues:
            errors[index] = issues
    return errors


def render_template(template: str, row: dict[str, str]) -> str:
    # {列名|後置文字} は値がある場合だけ「値＋後置文字」を出力する。
    # 例: {氏名2| 様} → "佐藤 様" または空文字
    template = CONDITIONAL_TAG_RE.sub(
        lambda match: (
            row.get(match.group(1), "") + match.group(2)
            if row.get(match.group(1), "") else ""
        ),
        template,
    )
    return TAG_RE.sub(lambda match: row.get(match.group(1), match.group(0)), template)


def unknown_tags(subject: str, body: str, headers: list[str]) -> list[str]:
    source = subject + "\n" + body
    tags = {
        tag.split("|", 1)[0]
        for tag in TAG_RE.findall(source)
    }
    return sorted(tags.difference(headers))


def match_individual_attachments(
        rows: list[dict[str, str]], match_columns: str | list[str] | tuple[str, ...],
        file_paths: list[str]) -> tuple[dict[int, list[str]], list[str]]:
    """1～複数列の値を「_」でつないでファイル名と照合する。

    「NO.」が「12」、「事業所名」が「山田商事」なら、
    12_山田商事.pdf、12_山田商事_請求書.pdf などが一致する。
    """
    columns = [match_columns] if isinstance(match_columns, str) else list(match_columns)
    result: dict[int, list[str]] = {}
    used: set[str] = set()
    for index, row in enumerate(rows):
        values = [row.get(column, "").strip() for column in columns]
        if not columns or any(not value for value in values):
            continue
        key = "_".join(values)
        matches = []
        for file_path in file_paths:
            stem = Path(file_path).stem.strip()
            if stem == key or any(
                    stem.startswith(key + separator)
                    for separator in ("_", "-", "－", " ", "　")):
                matches.append(file_path)
                used.add(file_path)
        if matches:
            result[index] = sorted(matches)
    unmatched = sorted(path for path in file_paths if path not in used)
    return result, unmatched
