from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from app.core import (
    carrier_domain_counts, export_recipient_file, guess_email_column,
    load_recipient_file, match_individual_attachments, normalize_search_text,
    render_template, split_addresses, typo_domain_suspects, unknown_tags,
    cyclic_neighbor, is_table_paste, parse_pasted_recipients, parse_pasted_rows,
    parse_pasted_table, sorted_row_order, validate_new_column_name, validate_rows,
)

HEADERS = ["事業所名", "役職名", "氏名", "メールアドレス"]


def test_parse_pasted_table_builds_headers_and_rows_from_first_line():
    headers, rows, notes = parse_pasted_table(
        "事業所名\t役職名\t氏名\tメールアドレス\n"
        "A社\t部長\t山田 太郎\ttaro@example.jp\n"
        "B社\t\t鈴木\thanako@example.jp")
    assert headers == HEADERS
    assert rows == [
        {"事業所名": "A社", "役職名": "部長", "氏名": "山田 太郎",
         "メールアドレス": "taro@example.jp"},
        {"事業所名": "B社", "役職名": "", "氏名": "鈴木",
         "メールアドレス": "hanako@example.jp"},
    ]
    assert notes == []


def test_parse_pasted_table_without_header_is_rejected():
    # 先頭行がアドレスを含むなら見出しが無いとみなし、追加せず案内する。
    with pytest.raises(ValueError, match="見出し"):
        parse_pasted_table("A社\t部長\t山田\ttaro@example.jp")


def test_parse_pasted_table_fixes_blank_and_duplicate_headers():
    headers, rows, _ = parse_pasted_table("名前\t\t名前\t\nA\tB\tC\t")
    assert headers == ["名前", "列2", "名前2"]   # 末尾の空見出しは捨てる
    assert rows == [{"名前": "A", "列2": "B", "名前2": "C"}]


def test_parse_pasted_table_cell_count_mismatch():
    headers, rows, notes = parse_pasted_table("a\tb\nx\ny\tz\textra")
    assert rows == [{"a": "x", "b": ""}, {"a": "y", "b": "z"}]
    assert any("無視" in note for note in notes)


def test_parse_pasted_table_skips_blank_lines_and_header_only():
    headers, rows, _ = parse_pasted_table("a\tb\n\n  \n1\t2\n")
    assert rows == [{"a": "1", "b": "2"}]
    assert parse_pasted_table("a\tb") == (["a", "b"], [], [])


def test_validate_new_column_name_accepts_normal_names():
    assert validate_new_column_name("部署名", HEADERS) is None
    assert validate_new_column_name("  備考 ", HEADERS) is None   # 前後の空白は無視


def test_validate_new_column_name_rejects_blank_and_duplicate():
    assert "入力" in validate_new_column_name("   ", HEADERS)
    assert "既に" in validate_new_column_name("氏名", HEADERS)
    assert "既に" in validate_new_column_name(" 氏名 ", HEADERS)


def test_validate_new_column_name_rejects_template_syntax_characters():
    # { } | は差し込みタグの書式で、列名に含めるとタグとして解釈できなくなる。
    for name in ("a{b", "a}b", "a|b"):
        assert "使えません" in validate_new_column_name(name, HEADERS)


def test_is_table_paste_detects_tab_only():
    assert is_table_paste("a\tb")
    assert not is_table_paste("a@example.jp, b@example.jp")


def test_parse_pasted_rows_maps_addresses_into_given_columns():
    rows = parse_pasted_rows("山田 <a@example.jp>; b@example.jp", HEADERS, "メールアドレス")
    assert rows == [
        {"事業所名": "", "役職名": "", "氏名": "山田", "メールアドレス": "a@example.jp"},
        {"事業所名": "", "役職名": "", "氏名": "", "メールアドレス": "b@example.jp"},
    ]


def test_parse_pasted_rows_without_name_column_keeps_address_only():
    rows = parse_pasted_rows("田中 <t@example.jp>", ["社名", "Mail"], "Mail")
    assert rows == [{"社名": "", "Mail": "t@example.jp"}]
    assert parse_pasted_rows("\n  \n", HEADERS, "メールアドレス") == []


def test_parse_pasted_recipients_plain_addresses_any_separator():
    text = "a@example.jp, b@example.jp;c@example.jp\nd@example.jp\n\n  "
    assert parse_pasted_recipients(text) == [
        ("", "a@example.jp"), ("", "b@example.jp"),
        ("", "c@example.jp"), ("", "d@example.jp")]


def test_parse_pasted_recipients_name_and_angle_bracket_forms():
    text = '山田 太郎 <taro@example.jp>; "鈴木, 花子" <hanako@example.jp>\n<x@example.jp>'
    assert parse_pasted_recipients(text) == [
        ("山田 太郎", "taro@example.jp"),
        ("鈴木, 花子", "hanako@example.jp"),
        ("", "x@example.jp")]


def test_parse_pasted_recipients_fullwidth_separators_and_name_before_address():
    text = "a@example.jp、b@example.jp；c@example.jp，山田 d@example.jp"
    assert parse_pasted_recipients(text) == [
        ("", "a@example.jp"), ("", "b@example.jp"),
        ("", "c@example.jp"), ("山田", "d@example.jp")]


def test_parse_pasted_recipients_keeps_invalid_chunks_for_later_validation():
    # アドレスとして不正でも捨てず、表の検証でエラー表示させる。
    assert parse_pasted_recipients("not-an-address\nmailto:m@example.jp") == [
        ("", "not-an-address"), ("", "m@example.jp")]
    assert parse_pasted_recipients("") == []


def test_cyclic_neighbor_moves_and_wraps():
    targets = [3, 10, 250]
    assert cyclic_neighbor(targets, 3, True) == 10
    assert cyclic_neighbor(targets, 4, True) == 10
    assert cyclic_neighbor(targets, 250, True) == 3      # 末尾から先頭へ
    assert cyclic_neighbor(targets, 10, False) == 3
    assert cyclic_neighbor(targets, 3, False) == 250     # 先頭から末尾へ
    assert cyclic_neighbor(targets, -1, True) == 3       # 未選択
    assert cyclic_neighbor(targets, -1, False) == 250


def test_cyclic_neighbor_single_and_empty():
    assert cyclic_neighbor([7], 7, True) == 7
    assert cyclic_neighbor([7], 7, False) == 7
    assert cyclic_neighbor([], 0, True) is None


def _names(rows, order):
    return [rows[i]["名"] for i in order]


def test_sorted_row_order_text_ascending_and_descending():
    rows = [{"名": "b"}, {"名": "A"}, {"名": "ｃ"}]
    # 大文字小文字・全角半角を区別せず並べる
    assert _names(rows, sorted_row_order(rows, "名", False)) == ["A", "b", "ｃ"]
    assert _names(rows, sorted_row_order(rows, "名", True)) == ["ｃ", "b", "A"]


def test_sorted_row_order_numbers_compare_as_numbers():
    rows = [{"名": "10"}, {"名": "9"}, {"名": "１００"}]
    assert _names(rows, sorted_row_order(rows, "名", False)) == ["9", "10", "１００"]


def test_sorted_row_order_empty_values_always_last_and_stable():
    rows = [{"名": ""}, {"名": "b"}, {"名": " "}, {"名": "a"}]
    assert sorted_row_order(rows, "名", False) == [3, 1, 0, 2]
    assert sorted_row_order(rows, "名", True) == [1, 3, 0, 2]


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


def test_normalize_search_text_ignores_kana_and_width_variants():
    assert normalize_search_text("やまだ ﾀﾛｳ") == normalize_search_text("ヤマダ　タロウ")
    assert normalize_search_text("ｻﾄｳ") == normalize_search_text("さとう")


def test_export_recipient_file_writes_headers_and_rows(tmp_path: Path):
    path = tmp_path / "out.xlsx"
    headers = ["氏名", "メール"]
    rows = [
        {"氏名": "山田", "メール": "a@example.jp"},
        {"氏名": "佐藤", "メール": "b@example.jp"},
    ]
    export_recipient_file(str(path), headers, rows)
    book = load_workbook(path)
    sheet = book.active
    values = [list(row) for row in sheet.iter_rows(values_only=True)]
    assert values == [
        ["氏名", "メール"],
        ["山田", "a@example.jp"],
        ["佐藤", "b@example.jp"],
    ]


def test_render_keeps_unknown_tags():
    assert render_template("{氏名} 様 {不明}", {"氏名": "山田"}) == "山田 様 {不明}"
    assert unknown_tags("", "{氏名}{不明}", ["氏名"]) == ["不明"]


def test_conditional_suffix_tag_hides_suffix_when_value_is_empty():
    template = "{氏名| 様}\n{氏名2| 様}"
    assert render_template(template, {"氏名": "山田", "氏名2": "佐藤"}) == "山田 様\n佐藤 様"
    assert render_template(template, {"氏名": "山田", "氏名2": ""}) == "山田 様\n"
    assert unknown_tags("", template, ["氏名", "氏名2"]) == []


def test_conditional_prefix_suffix_tag_hides_separator_when_value_is_empty():
    template = "{氏名A}様{、|氏名B|様}"
    assert render_template(
        template, {"氏名A": "山田", "氏名B": "佐藤"}) == "山田様、佐藤様"
    assert render_template(
        template, {"氏名A": "山田", "氏名B": ""}) == "山田様"
    assert unknown_tags("", template, ["氏名A", "氏名B"]) == []
    assert unknown_tags("", "{、|不明|様}", ["氏名A"]) == ["不明"]


def test_validate_rows_detects_invalid_and_duplicate():
    rows = [{"mail": "a@example.jp"}, {"mail": "a@example.jp"}, {"mail": "bad"}]
    errors = validate_rows(rows, "mail")
    assert 0 not in errors
    assert errors[1] == ["宛先が2行目と重複しています"]
    assert 2 in errors


def test_validate_rows_uses_actual_row_numbers_when_filtered():
    # 絞り込みで1・9・10行目だけを渡した場合、重複メッセージは
    # 渡した並び順ではなく実際のExcel行番号を指す。
    rows = [{"mail": "z@example.jp"}, {"mail": "a@example.jp"},
            {"mail": "a@example.jp"}]
    errors = validate_rows(rows, "mail", "", [2, 9, 10])
    assert errors == {2: ["宛先が9行目と重複しています"]}


def test_split_addresses():
    assert split_addresses("a@example.jp; b@example.jp,c@example.jp") == [
        "a@example.jp", "b@example.jp", "c@example.jp"
    ]


def test_typo_domain_suspects_flags_lookalike_domains():
    addresses = [
        "a@dokomo.ne.jp", "B@GMAI.COM", "c@docomo.ne.jp", "d@yahoo.ne.jp",
        "a@dokomo.ne.jp",
    ]
    suspects = typo_domain_suspects(addresses)
    # 正規のドメイン（docomo.ne.jp、Y!mobileのyahoo.ne.jp）は対象外。
    # 同じアドレスの重複は1件にまとめる。
    assert suspects == [("a@dokomo.ne.jp", "docomo.ne.jp"), ("B@GMAI.COM", "gmail.com")]


def test_carrier_domain_counts_groups_by_domain():
    addresses = [
        "a@docomo.ne.jp", "b@docomo.ne.jp", "c@ezweb.ne.jp",
        "d@example.co.jp", "e@gmail.com",
    ]
    assert carrier_domain_counts(addresses) == {"docomo.ne.jp": 2, "ezweb.ne.jp": 1}


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


def test_guess_email_column_matches_various_header_names():
    rows = [{"№": "1", "氏名": "山田", "値": "taro@example.co.jp"}]
    for header in ("メールアドレス", "アドレス", "E-mail", "Ｅメール", "送信先", "MAIL"):
        headers = ["№", "氏名", header]
        assert guess_email_column(headers, [{**rows[0], header: "taro@example.co.jp"}]) == header


def test_guess_email_column_falls_back_to_values_when_header_is_unclear():
    # 列名に手がかりが無くても、値がアドレスの列を選べる。
    headers = ["№", "氏名", "連絡"]
    rows = [{"№": str(i), "氏名": "山田", "連絡": f"user{i}@example.co.jp"}
            for i in range(10)]
    assert guess_email_column(headers, rows) == "連絡"


def test_guess_email_column_returns_empty_when_no_column_looks_like_email():
    # 判別できないときに先頭列へ倒れないことを保証する。
    # ここで「№」を返すと、番号列が宛先として採用され全行がエラーになる。
    headers = ["№", "氏名", "事業所名"]
    rows = [{"№": str(i), "氏名": "山田", "事業所名": "株式会社テスト"}
            for i in range(10)]
    assert guess_email_column(headers, rows) == ""


def test_guess_email_column_ignores_column_with_few_addresses():
    # 少数行だけアドレスらしい列を宛先に採用すると取り違えになる。
    headers = ["№", "備考"]
    rows = [{"№": str(i), "備考": "a@example.co.jp" if i < 2 else "なし"}
            for i in range(10)]
    assert guess_email_column(headers, rows) == ""
