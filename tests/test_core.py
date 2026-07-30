from pathlib import Path

from openpyxl import Workbook

from app.core import (
    load_recipient_file, match_individual_attachments, render_template,
    split_addresses, unknown_tags, validate_rows,
)


def test_load_xlsx_and_normalize(tmp_path: Path):
    path = tmp_path / "data.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.append(["事業所名", "人数", "メール"])
    sheet.append(["○○商事", 2, "a@example.jp"])
    book.save(path)
    result = load_recipient_file(str(path))
    assert result.headers == ["事業所名", "人数", "メール"]
    assert result.rows[0]["人数"] == "2"


def test_render_keeps_unknown_tags():
    assert render_template("{氏名} 様 {不明}", {"氏名": "山田"}) == "山田 様 {不明}"
    assert unknown_tags("", "{氏名}{不明}", ["氏名"]) == ["不明"]


def test_conditional_suffix_tag_hides_suffix_when_value_is_empty():
    template = "{氏名| 様}\n{氏名2| 様}"
    assert render_template(template, {"氏名": "山田", "氏名2": "佐藤"}) == "山田 様\n佐藤 様"
    assert render_template(template, {"氏名": "山田", "氏名2": ""}) == "山田 様\n"
    assert unknown_tags("", template, ["氏名", "氏名2"]) == []


def test_validate_rows_detects_invalid_and_duplicate():
    rows = [{"mail": "a@example.jp"}, {"mail": "a@example.jp"}, {"mail": "bad"}]
    errors = validate_rows(rows, "mail")
    assert 0 not in errors
    assert "重複" in errors[1][0]
    assert 2 in errors


def test_split_addresses():
    assert split_addresses("a@example.jp; b@example.jp,c@example.jp") == [
        "a@example.jp", "b@example.jp", "c@example.jp"
    ]


def test_match_individual_attachments_by_column_value(tmp_path):
    exact = tmp_path / "山田商事.pdf"
    extra = tmp_path / "山田商事_請求書.xlsx"
    other = tmp_path / "未登録会社.pdf"
    for path in (exact, extra, other):
        path.write_bytes(b"x")
    mapping, unmatched = match_individual_attachments(
        [{"事業所名": "山田商事"}, {"事業所名": "鈴木商店"}],
        "事業所名", [str(exact), str(extra), str(other)])
    assert mapping == {0: sorted([str(exact), str(extra)])}
    assert unmatched == [str(other)]


def test_match_individual_attachments_by_two_column_values(tmp_path):
    exact = tmp_path / "12_山田商事.pdf"
    extra = tmp_path / "12_山田商事_請求書.xlsx"
    wrong = tmp_path / "13_山田商事.pdf"
    for path in (exact, extra, wrong):
        path.write_bytes(b"x")
    mapping, unmatched = match_individual_attachments(
        [{"NO.": "12", "事業所名": "山田商事"},
         {"NO.": "13", "事業所名": ""}],
        ["NO.", "事業所名"], [str(exact), str(extra), str(wrong)])
    assert mapping == {0: sorted([str(exact), str(extra)])}
    assert unmatched == [str(wrong)]
