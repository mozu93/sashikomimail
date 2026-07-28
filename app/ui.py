from __future__ import annotations

import shutil
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDialog, QFileDialog, QFormLayout,
    QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QInputDialog, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QScrollArea, QSpinBox,
    QSplitter, QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.core import (
    is_valid_email, load_recipient_file, match_individual_attachments,
    render_template, split_addresses, unknown_tags, validate_rows,
)
from app.graph import (
    ATTACHMENT_LIMIT, get_access_token, get_cached_accounts, send_mail, sign_out,
)
from app.storage import Storage
from app.version import __version__

APP_STYLE = """
QMainWindow { background: #f4f7fb; }
QGroupBox { font-weight: bold; border: 1px solid #cbd5e1; border-radius: 7px;
            margin-top: 10px; padding-top: 12px; background: white; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #174a78; }
QPushButton { min-height: 28px; padding: 2px 12px; border-radius: 5px;
              border: 1px solid #9ca3af; background: #fff; }
QPushButton:hover { background: #edf4fb; }
QPushButton#primary { color: white; background: #1769aa; border-color: #1769aa; font-weight: bold; }
QPushButton#danger { color: white; background: #b42318; border-color: #b42318; }
QLineEdit, QComboBox, QPlainTextEdit, QSpinBox { border: 1px solid #aab4c0; border-radius: 4px;
                                               padding: 4px; background: white; }
QTabBar::tab { min-width: 120px; padding: 9px 16px; }
QHeaderView::section { background: #dce9f5; padding: 5px; border: 0; border-right: 1px solid #c4d3e0; }
"""


class SendWorker(QThread):
    progress = pyqtSignal(int, int, str)
    logged = pyqtSignal(int, str, str, str, str)
    completed = pyqtSignal(int, int, bool)
    failed = pyqtSignal(str)

    def __init__(self, config: dict, messages: list[dict], interval_ms: int):
        super().__init__()
        self.config, self.messages, self.interval_ms = config, messages, interval_ms
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def run(self):
        success = error = consecutive = 0
        try:
            token, _username = get_access_token(self.config)
            for index, message in enumerate(self.messages, 1):
                if self.cancelled:
                    break
                self.progress.emit(index - 1, len(self.messages), f"{index}件目を送信中")
                try:
                    graph_message = {
                        key: value for key, value in message.items()
                        if key != "row_number"
                    }
                    send_mail(self.config, token, **graph_message)
                    success += 1
                    consecutive = 0
                    self.logged.emit(message["row_number"], message["to_value"],
                                     message["subject"], "成功", "")
                except Exception as exc:
                    error += 1
                    consecutive += 1
                    self.logged.emit(message["row_number"], message["to_value"],
                                     message["subject"], "エラー", str(exc))
                    if consecutive >= 5:
                        self.failed.emit("5件連続で送信に失敗したため、安全のため中断しました。")
                        self.cancelled = True
                        break
                self.progress.emit(index, len(self.messages), f"{index}/{len(self.messages)}件")
                if index < len(self.messages) and self.interval_ms:
                    self.msleep(self.interval_ms)
        except Exception as exc:
            if self.messages and success == 0 and error == 0:
                message = self.messages[0]
                error = 1
                self.logged.emit(
                    message["row_number"], message["to_value"],
                    message["subject"], "エラー", str(exc))
            self.failed.emit(str(exc))
            self.cancelled = True
        self.completed.emit(success, error, self.cancelled)


class PreviewDialog(QDialog):
    def __init__(self, parent, title: str, content: str):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(680, 600)
        layout = QVBoxLayout(self)
        viewer = QPlainTextEdit(content)
        viewer.setReadOnly(True)
        layout.addWidget(viewer)
        close = QPushButton("閉じる")
        close.clicked.connect(self.accept)
        layout.addWidget(close)


class ComposeTab(QWidget):
    history_changed = pyqtSignal()
    sending_state_changed = pyqtSignal(bool)

    def __init__(self, storage: Storage):
        super().__init__()
        self.storage = storage
        self.headers: list[str] = []
        self.rows: list[dict[str, str]] = []
        self.attachments: list[str] = []
        self.individual_attachments: dict[int, list[str]] = {}
        self.individual_match_column = ""
        self.individual_folder = ""
        self.filtered_indices: list[int] = []
        self.source_path = ""
        self.worker: SendWorker | None = None
        self.job_id: int | None = None
        self.job_success = self.job_error = 0
        self.send_errors: list[str] = []
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        source = QGroupBox("1. 宛先データ")
        source_layout = QGridLayout(source)
        self.file_label = QLabel("ファイルが選択されていません")
        choose = QPushButton("Excel / CSVを選択")
        choose.setObjectName("primary")
        choose.clicked.connect(self.choose_file)
        save_list = QPushButton("現在の名簿を保存")
        save_list.clicked.connect(self.save_recipient_list)
        open_list = QPushButton("保存済み名簿を開く")
        open_list.clicked.connect(self.open_recipient_list)
        delete_list = QPushButton("保存済み名簿を削除")
        delete_list.clicked.connect(self.delete_recipient_list)
        source_layout.addWidget(choose, 0, 0)
        source_layout.addWidget(open_list, 0, 1)
        source_layout.addWidget(save_list, 0, 2)
        source_layout.addWidget(delete_list, 0, 3)
        source_layout.addWidget(self.file_label, 1, 0, 1, 4)
        source_layout.setColumnStretch(4, 1)
        root.addWidget(source)

        splitter = QSplitter()
        left = QWidget()
        left_layout = QVBoxLayout(left)
        preview_box = QGroupBox("2. データプレビュー（ダブルクリックで送信プレビュー）")
        preview_layout = QVBoxLayout(preview_box)
        filter_row = QHBoxLayout()
        self.filter_column = QComboBox()
        self.filter_operator = QComboBox()
        self.filter_operator.addItems(["含む", "完全一致", "空欄", "空欄でない"])
        self.filter_operator.currentTextChanged.connect(self.update_filter_input_state)
        self.filter_value = QLineEdit()
        self.filter_value.setPlaceholderText("絞り込む文字を入力")
        self.filter_value.returnPressed.connect(self.apply_filter)
        apply_filter_button = QPushButton("絞り込み")
        apply_filter_button.clicked.connect(self.apply_filter)
        clear_filter_button = QPushButton("解除")
        clear_filter_button.clicked.connect(self.clear_filter)
        filter_row.addWidget(QLabel("列"))
        filter_row.addWidget(self.filter_column)
        filter_row.addWidget(self.filter_operator)
        filter_row.addWidget(self.filter_value, 1)
        filter_row.addWidget(apply_filter_button)
        filter_row.addWidget(clear_filter_button)
        self.summary = QLabel("0件")
        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.doubleClicked.connect(self.preview_selected)
        preview_layout.addLayout(filter_row)
        preview_layout.addWidget(self.summary)
        preview_layout.addWidget(self.table)
        left_layout.addWidget(preview_box)
        splitter.addWidget(left)

        editor = QWidget()
        editor_layout = QVBoxLayout(editor)
        destination = QGroupBox("3. 宛先設定")
        form = QFormLayout(destination)
        self.to_column, self.cc_column = QComboBox(), QComboBox()
        self.to_column.currentTextChanged.connect(self.refresh_validation)
        self.cc_column.currentTextChanged.connect(self.refresh_validation)
        self.bcc = QLineEdit()
        self.bcc.setPlaceholderText("複数指定は ; または , で区切る")
        form.addRow("To列（必須）", self.to_column)
        form.addRow("CC列（任意）", self.cc_column)
        form.addRow("固定BCC", self.bcc)
        editor_layout.addWidget(destination)

        template = QGroupBox("4. 件名・本文")
        template_layout = QVBoxLayout(template)
        row = QHBoxLayout()
        self.template_combo = QComboBox()
        load = QPushButton("読込")
        load.clicked.connect(self.load_template)
        save = QPushButton("現在の件名・本文をテンプレート登録")
        save.setToolTip("入力中の件名と本文に名前を付けて保存します")
        save.clicked.connect(self.save_template)
        row.addWidget(QLabel("テンプレート"))
        row.addWidget(self.template_combo, 1)
        row.addWidget(load)
        row.addWidget(save)
        self.subject = QLineEdit()
        self.subject.setPlaceholderText("件名にも {列名} を使用できます")
        self.body = QPlainTextEdit()
        self.body.setPlaceholderText("本文を入力してください。例：{参加者名} 様")
        self.body.setMinimumHeight(150)
        template_layout.addLayout(row)
        template_layout.addWidget(QLabel("件名"))
        template_layout.addWidget(self.subject)
        template_layout.addWidget(QLabel("本文"))
        template_layout.addWidget(self.body)
        signature_row = QHBoxLayout()
        self.signature_combo = QComboBox()
        self.signature_combo.setToolTip("選択した署名を送信時に本文末尾へ追加します")
        signature_row.addWidget(QLabel("署名"))
        signature_row.addWidget(self.signature_combo, 1)
        signature_row.addWidget(QLabel("※署名タブで登録・編集"))
        template_layout.addLayout(signature_row)
        editor_layout.addWidget(template)

        tags = QGroupBox("利用可能タグ（ダブルクリックで本文へ挿入）")
        tags_layout = QVBoxLayout(tags)
        self.tag_list = QListWidget()
        self.tag_list.setMaximumHeight(105)
        self.tag_list.itemDoubleClicked.connect(
            lambda item: self.body.insertPlainText(item.text()))
        tags_layout.addWidget(self.tag_list)
        conditional_help = QLabel(
            "空欄時に敬称も消す場合:  {氏名2| 様}  "
            "（値があると「佐藤 様」、空欄なら何も表示しません）"
        )
        conditional_help.setStyleSheet("color: #475569; font-weight: normal;")
        tags_layout.addWidget(conditional_help)
        editor_layout.addWidget(tags)

        attach = QGroupBox("5. 共通添付")
        attach_layout = QHBoxLayout(attach)
        self.attach_label = QLabel("なし")
        self.attach_label.setMinimumWidth(180)
        self.attach_usage = QProgressBar()
        self.attach_usage.setRange(0, ATTACHMENT_LIMIT)
        self.attach_usage.setMinimumWidth(260)
        self.attach_usage.setFormat("0点 / 0.00 MB（残り 2.50 MB・点数上限なし）")
        add_attach = QPushButton("ファイルを追加")
        add_attach.clicked.connect(self.add_attachments)
        clear_attach = QPushButton("すべて解除")
        clear_attach.clicked.connect(self.clear_attachments)
        attach_layout.addWidget(add_attach)
        attach_layout.addWidget(clear_attach)
        attach_layout.addWidget(self.attach_label, 1)
        attach_layout.addWidget(self.attach_usage)
        editor_layout.addWidget(attach)

        individual = QGroupBox("6. 事業所別・個別添付")
        individual_layout = QHBoxLayout(individual)
        set_individual = QPushButton("個別添付フォルダを選択")
        set_individual.clicked.connect(self.set_individual_attachments)
        clear_individual = QPushButton("個別添付を解除")
        clear_individual.clicked.connect(self.clear_individual_attachments)
        self.individual_label = QLabel(
            "未設定（事業所名などの列とファイル名を照合します）")
        individual_layout.addWidget(set_individual)
        individual_layout.addWidget(clear_individual)
        individual_layout.addWidget(self.individual_label, 1)
        editor_layout.addWidget(individual)
        editor_scroll = QScrollArea()
        editor_scroll.setWidgetResizable(True)
        editor_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        editor_scroll.setWidget(editor)
        editor_scroll.setMinimumWidth(480)
        splitter.addWidget(editor_scroll)
        splitter.setSizes([760, 520])
        root.addWidget(splitter, 1)

        controls = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setFormat("%v / %m件")
        preview = QPushButton("選択行をプレビュー")
        preview.clicked.connect(self.preview_selected)
        self.test_button = QPushButton("テスト送信")
        self.test_button.clicked.connect(self.test_send)
        self.send_button = QPushButton("一括送信")
        self.send_button.setObjectName("primary")
        self.send_button.clicked.connect(self.start_send)
        self.cancel_button = QPushButton("送信を中止")
        self.cancel_button.setObjectName("danger")
        self.cancel_button.clicked.connect(self.cancel_send)
        self.cancel_button.hide()
        controls.addWidget(self.progress, 1)
        controls.addWidget(preview)
        controls.addWidget(self.test_button)
        controls.addWidget(self.send_button)
        controls.addWidget(self.cancel_button)
        root.addLayout(controls)
        self.send_lock_widgets = [
            source, splitter, preview, self.test_button, self.send_button,
        ]
        self.refresh_templates()
        self.refresh_signatures()

    def refresh_templates(self):
        current = self.template_combo.currentText()
        self.template_combo.clear()
        for template in self.storage.templates():
            self.template_combo.addItem(template["name"], template)
        index = self.template_combo.findText(current)
        if index >= 0:
            self.template_combo.setCurrentIndex(index)

    def refresh_signatures(self):
        current = self.signature_combo.currentText()
        self.signature_combo.clear()
        self.signature_combo.addItem("署名なし", "")
        for signature in self.storage.signatures():
            self.signature_combo.addItem(signature["name"], signature["body"])
        index = self.signature_combo.findText(current)
        self.signature_combo.setCurrentIndex(index if index >= 0 else 0)

    def choose_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "宛先データを選択", "", "宛先データ (*.xlsx *.xls *.csv)")
        if not path:
            return
        try:
            result = load_recipient_file(path)
        except Exception as exc:
            QMessageBox.critical(self, "読込エラー", str(exc))
            return
        self.apply_recipient_data(path, result.headers, result.rows)
        if result.warnings:
            QMessageBox.warning(self, "読込時の注意", "\n".join(result.warnings))

    def apply_recipient_data(self, source_path: str, headers: list[str],
                             rows: list[dict[str, str]], display_name: str = ""):
        self.source_path, self.headers, self.rows = source_path, headers, rows
        self.clear_individual_attachments()
        self.filtered_indices = list(range(len(rows)))
        source_name = display_name or Path(source_path).name
        self.file_label.setText(f"{source_name}（{len(self.rows)}件）")
        self.to_column.clear()
        self.to_column.addItems(self.headers)
        self.cc_column.clear()
        self.cc_column.addItem("")
        self.cc_column.addItems(self.headers)
        self.filter_column.clear()
        self.filter_column.addItems(self.headers)
        self.filter_value.clear()
        guessed = next((h for h in self.headers if "メール" in h or "mail" in h.lower()), "")
        if guessed:
            self.to_column.setCurrentText(guessed)
        self.tag_list.clear()
        self.tag_list.addItems([f"{{{header}}}" for header in self.headers])
        self.render_table()

    def save_recipient_list(self):
        if not self.rows:
            QMessageBox.warning(self, "名簿保存", "先にExcelまたはCSVを読み込んでください。")
            return
        default_name = Path(self.source_path).stem if self.source_path else ""
        name, ok = QInputDialog.getText(
            self, "名簿を保存", "名簿名:", QLineEdit.EchoMode.Normal, default_name)
        name = name.strip()
        if not ok or not name:
            return
        existing = next((item for item in self.storage.recipient_lists()
                         if item["name"] == name), None)
        if existing and QMessageBox.question(
                self, "上書き確認", f"名簿「{name}」を更新しますか？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.storage.save_recipient_list(
            name, Path(self.source_path).name, self.headers, self.rows)
        QMessageBox.information(self, "名簿保存", f"名簿「{name}」を保存しました。")

    def open_recipient_list(self):
        saved = self.storage.recipient_lists()
        if not saved:
            QMessageBox.information(self, "保存済み名簿", "保存済みの名簿はありません。")
            return
        labels = [
            f"{item['name']}（{len(item['rows'])}件・{item['updated_at'].replace('T', ' ')}）"
            for item in saved
        ]
        selected, ok = QInputDialog.getItem(
            self, "保存済み名簿を開く", "名簿:", labels, 0, False)
        if not ok:
            return
        item = saved[labels.index(selected)]
        self.apply_recipient_data(
            item["source_name"], item["headers"], item["rows"],
            f"保存済み: {item['name']}")

    def delete_recipient_list(self):
        saved = self.storage.recipient_lists()
        if not saved:
            QMessageBox.information(self, "名簿削除", "保存済みの名簿はありません。")
            return
        labels = [f"{item['name']}（{len(item['rows'])}件）" for item in saved]
        selected, ok = QInputDialog.getItem(
            self, "保存済み名簿を削除", "削除する名簿:", labels, 0, False)
        if not ok:
            return
        item = saved[labels.index(selected)]
        answer = QMessageBox.question(
            self, "名簿削除の確認",
            f"名簿「{item['name']}」（{len(item['rows'])}件）を削除します。\n"
            "この操作は元に戻せません。削除してよろしいですか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.storage.delete_recipient_list(item["id"])
        QMessageBox.information(self, "名簿削除", f"名簿「{item['name']}」を削除しました。")

    def render_table(self):
        self.table.setColumnCount(len(self.headers) + 1)
        self.table.setHorizontalHeaderLabels(["状態"] + self.headers)
        self.table.setRowCount(len(self.rows))
        for r, row in enumerate(self.rows):
            self.table.setItem(r, 0, QTableWidgetItem(""))
            for c, header in enumerate(self.headers, 1):
                self.table.setItem(r, c, QTableWidgetItem(row.get(header, "")))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        if self.rows:
            self.table.selectRow(0)
        self.refresh_validation()

    def refresh_validation(self):
        if not self.rows or not self.to_column.currentText():
            return
        indices = self.filtered_indices or []
        target_rows = [self.rows[index] for index in indices]
        subset_errors = validate_rows(
            target_rows, self.to_column.currentText(), self.cc_column.currentText())
        errors = {indices[index]: value for index, value in subset_errors.items()}
        for row_index in range(len(self.rows)):
            item = self.table.item(row_index, 0)
            if not item:
                continue
            if row_index in errors:
                item.setText("エラー")
                item.setToolTip("\n".join(errors[row_index]))
                for column in range(self.table.columnCount()):
                    self.table.item(row_index, column).setBackground(QColor("#fee2e2"))
            else:
                item.setText("OK")
                item.setToolTip("")
                for column in range(self.table.columnCount()):
                    self.table.item(row_index, column).setBackground(QColor("white"))
        self.summary.setText(
            f"表示・送信対象 {len(indices)}件 / 全{len(self.rows)}件"
            f"（対象内エラー {len(errors)}件）")

    def update_filter_input_state(self):
        needs_value = self.filter_operator.currentText() in ("含む", "完全一致")
        self.filter_value.setEnabled(needs_value)

    def apply_filter(self):
        if not self.rows or not self.filter_column.currentText():
            return
        column = self.filter_column.currentText()
        operator = self.filter_operator.currentText()
        needle = self.filter_value.text().strip().casefold()
        if operator in ("含む", "完全一致") and not needle:
            QMessageBox.information(self, "絞り込み", "絞り込む文字を入力してください。")
            return
        matched = []
        for index, row in enumerate(self.rows):
            value = row.get(column, "").strip()
            normalized = value.casefold()
            include = (
                (operator == "含む" and needle in normalized)
                or (operator == "完全一致" and normalized == needle)
                or (operator == "空欄" and not value)
                or (operator == "空欄でない" and bool(value))
            )
            self.table.setRowHidden(index, not include)
            if include:
                matched.append(index)
        self.filtered_indices = matched
        if matched:
            self.table.selectRow(matched[0])
        else:
            self.table.clearSelection()
        self.refresh_validation()

    def clear_filter(self):
        self.filtered_indices = list(range(len(self.rows)))
        for index in range(len(self.rows)):
            self.table.setRowHidden(index, False)
        self.filter_value.clear()
        if self.rows:
            self.table.selectRow(0)
        self.refresh_validation()

    def load_template(self):
        data = self.template_combo.currentData()
        if data:
            self.subject.setText(data["subject"])
            self.body.setPlainText(data["body"])

    def save_template(self):
        if not self.subject.text().strip() and not self.body.toPlainText().strip():
            QMessageBox.warning(
                self, "テンプレート登録", "登録する件名または本文を入力してください。")
            return
        name, ok = TextPrompt.get(self, "テンプレート保存", "テンプレート名")
        if not ok or not name.strip():
            return
        existing = next(
            (item for item in self.storage.templates() if item["name"] == name.strip()),
            None,
        )
        if existing and QMessageBox.question(
                self, "上書き確認",
                f"テンプレート「{name.strip()}」を現在の件名・本文で更新しますか？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.storage.save_template(name.strip(), self.subject.text(), self.body.toPlainText())
        self.refresh_templates()
        self.template_combo.setCurrentText(name.strip())
        QMessageBox.information(
            self, "テンプレート登録",
            f"テンプレート「{name.strip()}」を登録しました。")

    def add_attachments(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "添付ファイルを選択")
        rejected = []
        for path in paths:
            if path not in self.attachments:
                proposed_size = self.attachment_total_size() + Path(path).stat().st_size
                if proposed_size > ATTACHMENT_LIMIT:
                    rejected.append(Path(path).name)
                else:
                    self.attachments.append(path)
        self.refresh_attachment_display()
        if rejected:
            QMessageBox.warning(
                self, "添付容量の上限",
                "安全上限の合計2.5MBを超えるため、次のファイルは追加しませんでした。\n\n"
                + "\n".join(rejected)
                + "\n\n大きなファイルは圧縮するか、クラウド共有リンクを本文へ記載してください。"
            )

    def clear_attachments(self):
        self.attachments.clear()
        self.refresh_attachment_display()

    def attachment_total_size(self) -> int:
        return sum(
            Path(path).stat().st_size
            for path in self.attachments
            if Path(path).is_file()
        )

    def refresh_attachment_display(self):
        total = self.attachment_total_size()
        count = len(self.attachments)
        mb = total / (1024 * 1024)
        remaining = max(ATTACHMENT_LIMIT - total, 0) / (1024 * 1024)
        self.attach_label.setText(
            "、".join(Path(path).name for path in self.attachments) or "添付なし")
        self.attach_label.setToolTip("\n".join(
            f"{Path(path).name}  ({Path(path).stat().st_size / (1024 * 1024):.2f} MB)"
            for path in self.attachments if Path(path).is_file()
        ))
        self.attach_usage.setValue(min(total, ATTACHMENT_LIMIT))
        self.attach_usage.setFormat(
            f"{count}点 / {mb:.2f} MB（残り {remaining:.2f} MB・点数上限なし）")

    def set_individual_attachments(self):
        if not self.rows:
            QMessageBox.warning(
                self, "個別添付", "先にExcel、CSVまたは保存済み名簿を開いてください。")
            return
        column, ok = QInputDialog.getItem(
            self, "個別添付の照合列", "ファイル名と照合する列:",
            self.headers, 0, False)
        if not ok:
            return
        folder = QFileDialog.getExistingDirectory(
            self, "事業所ごとのファイルが入ったフォルダを選択")
        if not folder:
            return
        file_paths = [
            str(path) for path in Path(folder).iterdir()
            if path.is_file() and not path.name.startswith("~$")
        ]
        mapping, unmatched = match_individual_attachments(
            self.rows, column, file_paths)
        self.individual_attachments = mapping
        self.individual_match_column = column
        self.individual_folder = folder
        file_count = sum(len(paths) for paths in mapping.values())
        self.individual_label.setText(
            f"{column}で照合：{len(mapping)}/{len(self.rows)}件に"
            f"{file_count}ファイルを割当")
        self.individual_label.setToolTip(
            f"フォルダ: {folder}\n未一致ファイル: {len(unmatched)}件")
        details = (
            f"{len(self.rows)}件中 {len(mapping)}件へ、"
            f"合計{file_count}ファイルを割り当てました。"
        )
        if unmatched:
            preview = "\n".join(Path(path).name for path in unmatched[:10])
            details += f"\n\n一致しなかったファイル: {len(unmatched)}件\n{preview}"
            if len(unmatched) > 10:
                details += f"\nほか {len(unmatched) - 10}件"
        QMessageBox.information(self, "個別添付の照合結果", details)

    def clear_individual_attachments(self):
        self.individual_attachments = {}
        self.individual_match_column = ""
        self.individual_folder = ""
        if hasattr(self, "individual_label"):
            self.individual_label.setText(
                "未設定（事業所名などの列とファイル名を照合します）")
            self.individual_label.setToolTip("")

    def current_row(self) -> tuple[int, dict[str, str]] | None:
        index = self.table.currentRow()
        return (
            (index, self.rows[index])
            if 0 <= index < len(self.rows) and not self.table.isRowHidden(index)
            else None
        )

    def message_for(self, index: int, row: dict[str, str], test_to: str = "") -> dict:
        to_value = test_to or row.get(self.to_column.currentText(), "")
        body = render_template(self.body.toPlainText(), row)
        signature = self.signature_combo.currentData() or ""
        if signature:
            body = body.rstrip() + "\n\n" + render_template(signature, row)
        return {
            "row_number": index + 2,
            "to_value": to_value,
            "cc_value": "" if test_to else row.get(self.cc_column.currentText(), ""),
            "bcc_value": "" if test_to else self.bcc.text().strip(),
            "subject": render_template(self.subject.text(), row),
            "body": body,
            "attachment_paths": (
                list(self.attachments)
                + list(self.individual_attachments.get(index, []))
            ),
        }

    def preview_selected(self):
        selected = self.current_row()
        if not selected:
            QMessageBox.information(self, "プレビュー", "プレビューする行を選択してください。")
            return
        message = self.message_for(*selected)
        content = (
            f"To: {message['to_value']}\nCC: {message['cc_value']}\n"
            f"BCC: {message['bcc_value']}\n件名: {message['subject']}\n"
            f"添付: {', '.join(Path(x).name for x in message['attachment_paths']) or 'なし'}\n\n"
            f"{message['body']}"
        )
        PreviewDialog(self, "送信プレビュー", content).exec()

    def preflight(self) -> list[dict] | None:
        if not self.rows:
            QMessageBox.warning(self, "確認", "宛先データを読み込んでください。")
            return None
        target_indices = list(self.filtered_indices)
        if not target_indices:
            QMessageBox.warning(self, "確認", "絞り込み後の送信対象が0件です。")
            return None
        target_rows = [self.rows[index] for index in target_indices]
        errors = validate_rows(
            target_rows, self.to_column.currentText(), self.cc_column.currentText())
        if errors:
            QMessageBox.warning(self, "確認", f"エラー行が{len(errors)}件あります。修正したファイルを再読込してください。")
            return None
        bad_bcc = [x for x in split_addresses(self.bcc.text()) if not is_valid_email(x)]
        if bad_bcc:
            QMessageBox.warning(self, "確認", "固定BCCの形式が不正です。")
            return None
        signature = self.signature_combo.currentData() or ""
        unknown = unknown_tags(
            self.subject.text(), self.body.toPlainText() + "\n" + signature,
            self.headers)
        if unknown:
            QMessageBox.warning(self, "確認", "未定義のタグがあります: " + "、".join(f"{{{x}}}" for x in unknown))
            return None
        if not self.subject.text().strip() or not self.body.toPlainText().strip():
            QMessageBox.warning(self, "確認", "件名と本文を入力してください。")
            return None
        missing = [x for x in self.attachments if not Path(x).is_file()]
        missing += [
            path for paths in self.individual_attachments.values()
            for path in paths if not Path(path).is_file()
        ]
        if missing:
            QMessageBox.warning(self, "確認", "添付ファイルが見つかりません:\n" + "\n".join(missing))
            return None
        oversized_rows = []
        for index in target_indices:
            paths = self.attachments + self.individual_attachments.get(index, [])
            size = sum(Path(path).stat().st_size for path in paths)
            if size > ATTACHMENT_LIMIT:
                oversized_rows.append(
                    f"{index + 2}行目: {size / (1024 * 1024):.2f} MB")
        if oversized_rows:
            QMessageBox.warning(
                self, "添付容量の上限",
                "共通添付と個別添付の合計が安全上限の2.5MBを超える行があります。\n\n"
                + "\n".join(oversized_rows[:10]))
            return None
        return [
            self.message_for(index, self.rows[index])
            for index in target_indices
        ]

    def test_send(self):
        if self.worker and self.worker.isRunning():
            return
        messages = self.preflight()
        if not messages:
            return
        settings = self.storage.settings()
        address = settings.get("test_address", "")
        if not is_valid_email(address):
            QMessageBox.warning(self, "テスト送信", "設定画面で正しいテスト送信先を登録してください。")
            return
        selected = self.current_row() or (0, self.rows[0])
        message = self.message_for(*selected, test_to=address)
        message["subject"] = "【テスト】" + message["subject"]
        self.job_id = self.storage.start_job(
            Path(self.source_path).name if self.source_path else "保存済み名簿",
            message["subject"], 1, job_type="test", messages=[message])
        self.launch_worker([message], test=True)

    def start_send(self):
        if self.worker and self.worker.isRunning():
            return
        messages = self.preflight()
        if not messages:
            return
        settings = self.storage.settings()
        account = settings.get("account_username", "") or "未確認"
        if account == "未確認":
            QMessageBox.warning(
                self, "送信アカウント未確認",
                "設定タブで「サインイン／確認」を実行し、"
                "送信アカウントを確認してから一括送信してください。")
            return
        from_address = settings.get("from_address", "") or account
        answer = QMessageBox.question(
            self, "一括送信の最終確認",
            f"{len(messages)}件を1件ずつ個別送信します。\n\n"
            f"認証アカウント: {account}\n"
            f"差出人: {from_address}\n\n"
            "・案内の送信を了承している宛先ですか？\n"
            "・テスト送信で内容を確認しましたか？\n"
            "・不達になったアドレスを名簿から整理していますか？\n\n"
            "実行してよろしいですか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self.job_id = self.storage.start_job(
                Path(self.source_path).name, self.subject.text(), len(messages),
                messages=messages)
            self.launch_worker(messages)

    def launch_worker(self, messages: list[dict], test: bool = False):
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "送信中", "別の送信処理が実行中です。")
            return
        settings = self.storage.settings()
        self.send_errors = []
        interval_ms = max(int(settings.get("interval_ms", 2000)), 2000)
        self.worker = SendWorker(settings, messages, interval_ms)
        self.worker.progress.connect(self.on_progress)
        self.worker.logged.connect(
            lambda row, address, subject, status, error:
            self.on_logged(row, address, subject, status, error, test)
        )
        self.worker.failed.connect(lambda text: QMessageBox.critical(self, "送信エラー", text))
        self.worker.completed.connect(lambda s, e, c: self.on_complete(s, e, c, test))
        self.progress.setRange(0, len(messages))
        for widget in self.send_lock_widgets:
            widget.setEnabled(False)
        self.cancel_button.show()
        self.sending_state_changed.emit(True)
        self.worker.start()

    def resend_messages(self, messages: list[dict]):
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "再送信", "別の送信処理が実行中です。")
            return
        if not messages:
            QMessageBox.information(self, "再送信", "再送信対象はありません。")
            return
        missing = [
            path for message in messages
            for path in message.get("attachment_paths", [])
            if not Path(path).is_file()
        ]
        if missing:
            QMessageBox.warning(
                self, "再送信",
                "添付ファイルが見つからないため再送信できません。\n\n"
                + "\n".join(sorted(set(missing))))
            return
        oversized = []
        for message in messages:
            total = sum(
                Path(path).stat().st_size
                for path in message.get("attachment_paths", [])
            )
            if total > ATTACHMENT_LIMIT:
                oversized.append(
                    f"{message.get('to_value', '')}: "
                    f"{total / (1024 * 1024):.2f} MB")
        if oversized:
            QMessageBox.warning(
                self, "再送信",
                "安全上限の2.5MBを超える添付があります。\n\n"
                + "\n".join(oversized[:10]))
            return
        answer = QMessageBox.question(
            self, "エラー・未送信の再送信",
            f"エラーまたは未送信の{len(messages)}件を再送信しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        subject = messages[0].get("subject", "再送信")
        self.job_id = self.storage.start_job(
            "履歴から再送", subject, len(messages), job_type="retry",
            messages=messages)
        self.launch_worker(messages)

    def on_progress(self, value: int, total: int, text: str):
        self.progress.setMaximum(total)
        self.progress.setValue(value)
        self.progress.setToolTip(text)

    def on_logged(self, row_number: int, to_address: str, subject: str,
                  status: str, error: str, test: bool = False):
        if error:
            self.send_errors.append(f"{to_address}\n{error}")
        if self.job_id:
            self.storage.add_log(self.job_id, row_number, to_address, subject, status, error)

    def cancel_send(self):
        if self.worker:
            self.worker.cancel()
            self.cancel_button.setEnabled(False)

    def on_complete(self, success: int, error: int, cancelled: bool, test: bool):
        if self.job_id:
            self.storage.finish_job(self.job_id, success, error, cancelled)
            self.history_changed.emit()
        self.worker = None
        self.job_id = None
        for widget in self.send_lock_widgets:
            widget.setEnabled(True)
        self.cancel_button.setEnabled(True)
        self.cancel_button.hide()
        self.sending_state_changed.emit(False)
        status = "中断" if cancelled else "完了"
        result = f"{status}\n成功: {success}件\nエラー: {error}件"
        if self.send_errors:
            result += "\n\nエラー詳細:\n" + "\n\n".join(self.send_errors[:10])
            if len(self.send_errors) > 10:
                result += f"\n\nほか {len(self.send_errors) - 10}件"
        if error:
            QMessageBox.critical(self, "送信結果", result)
        else:
            QMessageBox.information(self, "送信結果", result)


class TextPrompt(QDialog):
    @staticmethod
    def get(parent, title: str, label: str):
        dialog = QDialog(parent)
        dialog.setWindowTitle(title)
        layout = QFormLayout(dialog)
        edit = QLineEdit()
        layout.addRow(label, edit)
        buttons = QHBoxLayout()
        ok, cancel = QPushButton("OK"), QPushButton("キャンセル")
        ok.clicked.connect(dialog.accept)
        cancel.clicked.connect(dialog.reject)
        buttons.addWidget(ok)
        buttons.addWidget(cancel)
        layout.addRow(buttons)
        result = dialog.exec() == QDialog.DialogCode.Accepted
        return edit.text(), result


class TemplateTab(QWidget):
    changed = pyqtSignal()

    def __init__(self, storage: Storage):
        super().__init__()
        self.storage = storage
        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["テンプレート名", "件名", "更新日時"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        buttons = QHBoxLayout()
        delete = QPushButton("選択したテンプレートを削除")
        delete.clicked.connect(self.delete)
        buttons.addStretch()
        buttons.addWidget(delete)
        layout.addWidget(QLabel("作成・送信画面で保存したテンプレートを管理します。"))
        layout.addWidget(self.table)
        layout.addLayout(buttons)

    def refresh(self):
        templates = self.storage.templates()
        self.table.setRowCount(len(templates))
        for r, template in enumerate(templates):
            item = QTableWidgetItem(template["name"])
            item.setData(256, template["id"])
            self.table.setItem(r, 0, item)
            self.table.setItem(r, 1, QTableWidgetItem(template["subject"]))
            self.table.setItem(r, 2, QTableWidgetItem(template["updated_at"]))

    def delete(self):
        row = self.table.currentRow()
        if row < 0:
            return
        if QMessageBox.question(self, "削除確認", "選択したテンプレートを削除しますか？",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.storage.delete_template(self.table.item(row, 0).data(256))
            self.refresh()
            self.changed.emit()


class HistoryTab(QWidget):
    resend_requested = pyqtSignal(list)

    def __init__(self, storage: Storage):
        super().__init__()
        self.storage = storage
        layout = QVBoxLayout(self)
        self.jobs = QTableWidget(0, 8)
        self.jobs.setHorizontalHeaderLabels(
            ["種別", "日時", "ファイル", "件名", "総数", "成功", "エラー", "状態"])
        self.jobs.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.jobs.itemSelectionChanged.connect(self.show_logs)
        self.logs = QTableWidget(0, 5)
        self.logs.setHorizontalHeaderLabels(["元データ行", "宛先", "件名", "結果", "エラー内容"])
        self.logs.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        splitter = QSplitter()
        splitter.addWidget(self.jobs)
        splitter.addWidget(self.logs)
        splitter.setOrientation(__import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.Orientation.Vertical)
        buttons = QHBoxLayout()
        delete = QPushButton("選択した履歴を削除")
        delete.clicked.connect(self.delete_selected)
        retry = QPushButton("エラー・未送信を再送")
        retry.clicked.connect(self.retry_selected)
        buttons.addStretch()
        buttons.addWidget(retry)
        buttons.addWidget(delete)
        layout.addWidget(QLabel("送信履歴（エラー宛先は内容を確認後、元データを修正して再送信できます）"))
        layout.addWidget(splitter)
        layout.addLayout(buttons)

    def refresh(self):
        jobs = self.storage.jobs()
        self.jobs.setRowCount(len(jobs))
        for r, job in enumerate(jobs):
            type_label = {"test": "テスト", "retry": "再送", "bulk": "一括"}
            values = [type_label.get(job[8], "一括"),
                      job[1], job[2], job[3], job[4], job[5], job[6],
                      "中断" if job[7] else "完了"]
            for c, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if c == 0:
                    item.setData(256, job[0])
                self.jobs.setItem(r, c, item)
        self.jobs.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.logs.setRowCount(0)

    def show_logs(self):
        row = self.jobs.currentRow()
        if row < 0:
            return
        logs = self.storage.logs(self.jobs.item(row, 0).data(256))
        self.logs.setRowCount(len(logs))
        for r, log in enumerate(logs):
            for c, value in enumerate(log):
                self.logs.setItem(r, c, QTableWidgetItem(str(value)))

    def delete_selected(self):
        row = self.jobs.currentRow()
        if row < 0:
            QMessageBox.information(self, "履歴削除", "削除する履歴を選択してください。")
            return
        job_id = self.jobs.item(row, 0).data(256)
        if QMessageBox.question(
                self, "履歴削除の確認",
                "選択した送信履歴と明細を削除しますか？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.storage.delete_job(job_id)
        self.refresh()

    def retry_selected(self):
        row = self.jobs.currentRow()
        if row < 0:
            QMessageBox.information(self, "再送信", "履歴を選択してください。")
            return
        job_id = self.jobs.item(row, 0).data(256)
        messages = self.storage.retry_messages(job_id)
        if not messages:
            QMessageBox.information(
                self, "再送信", "この履歴にエラーまたは未送信はありません。")
            return
        self.resend_requested.emit(messages)


class SignatureTab(QWidget):
    changed = pyqtSignal()

    def __init__(self, storage: Storage):
        super().__init__()
        self.storage = storage
        self.current_id: int | None = None
        root = QHBoxLayout(self)

        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self.load_selected)
        new_button = QPushButton("新しい署名")
        new_button.clicked.connect(self.new_signature)
        delete_button = QPushButton("選択した署名を削除")
        delete_button.clicked.connect(self.delete_signature)
        left.addWidget(QLabel("登録済み署名"))
        left.addWidget(self.list)
        left.addWidget(new_button)
        left.addWidget(delete_button)

        editor_box = QGroupBox("署名の登録・編集")
        editor = QVBoxLayout(editor_box)
        self.name = QLineEdit()
        self.name.setPlaceholderText("例：総務部 山田")
        self.body = QPlainTextEdit()
        self.body.setPlaceholderText(
            "――――――――――\n○○商工会議所 総務部\n山田 太郎\n"
            "TEL: 000-000-0000\nE-mail: example@example.jp")
        save = QPushButton("署名を保存")
        save.setObjectName("primary")
        save.clicked.connect(self.save_signature)
        editor.addWidget(QLabel("署名名"))
        editor.addWidget(self.name)
        editor.addWidget(QLabel("署名本文"))
        editor.addWidget(self.body, 1)
        editor.addWidget(QLabel(
            "署名本文でも、名簿の差し込みタグを使用できます。"))
        editor.addWidget(save)

        left_widget = QWidget()
        left_widget.setLayout(left)
        left_widget.setMaximumWidth(330)
        root.addWidget(left_widget)
        root.addWidget(editor_box, 1)
        self.refresh()

    def refresh(self):
        selected_id = self.current_id
        self.list.clear()
        for signature in self.storage.signatures():
            self.list.addItem(signature["name"])
            self.list.item(self.list.count() - 1).setData(256, signature)
        if selected_id:
            for index in range(self.list.count()):
                if self.list.item(index).data(256)["id"] == selected_id:
                    self.list.setCurrentRow(index)
                    break

    def load_selected(self, row: int):
        if row < 0:
            return
        signature = self.list.item(row).data(256)
        self.current_id = signature["id"]
        self.name.setText(signature["name"])
        self.body.setPlainText(signature["body"])

    def new_signature(self):
        self.current_id = None
        self.list.clearSelection()
        self.name.clear()
        self.body.clear()
        self.name.setFocus()

    def save_signature(self):
        name = self.name.text().strip()
        body = self.body.toPlainText().strip()
        if not name or not body:
            QMessageBox.warning(
                self, "署名登録", "署名名と署名本文を入力してください。")
            return
        existing = next(
            (item for item in self.storage.signatures()
             if item["name"] == name and item["id"] != self.current_id),
            None)
        if existing and QMessageBox.question(
                self, "上書き確認", f"署名「{name}」を更新しますか？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        if existing and self.current_id and existing["id"] != self.current_id:
            self.storage.delete_signature(self.current_id)
            self.current_id = existing["id"]
        self.storage.save_signature(name, body, self.current_id)
        self.current_id = next(
            item["id"] for item in self.storage.signatures()
            if item["name"] == name)
        self.refresh()
        self.changed.emit()
        QMessageBox.information(self, "署名登録", f"署名「{name}」を保存しました。")

    def delete_signature(self):
        row = self.list.currentRow()
        if row < 0:
            QMessageBox.information(self, "署名削除", "削除する署名を選択してください。")
            return
        signature = self.list.item(row).data(256)
        if QMessageBox.question(
                self, "署名削除の確認", f"署名「{signature['name']}」を削除しますか？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.storage.delete_signature(signature["id"])
        self.new_signature()
        self.refresh()
        self.changed.emit()


class SettingsTab(QWidget):
    def __init__(self, storage: Storage):
        super().__init__()
        self.storage = storage
        layout = QVBoxLayout(self)
        box = QGroupBox("Microsoft 365 / Microsoft Graph API")
        form = QFormLayout(box)
        self.tenant = QLineEdit()
        self.client = QLineEdit()
        self.from_address = QLineEdit()
        self.test_address = QLineEdit()
        self.account = QComboBox()
        self.account.setEditable(False)
        self.interval = QSpinBox()
        self.interval.setRange(2000, 10000)
        self.interval.setSingleStep(500)
        self.interval.setSuffix(" ms")
        self.retention_days = QSpinBox()
        self.retention_days.setRange(30, 3650)
        self.retention_days.setSuffix(" 日")
        form.addRow("テナントID", self.tenant)
        form.addRow("クライアントID", self.client)
        form.addRow("代理差出人（任意）", self.from_address)
        form.addRow("テスト送信先", self.test_address)
        account_row = QHBoxLayout()
        account_row.addWidget(self.account, 1)
        sign_in_button = QPushButton("サインイン／確認")
        sign_in_button.clicked.connect(self.authenticate_account)
        sign_out_button = QPushButton("サインアウト")
        sign_out_button.clicked.connect(self.logout_account)
        account_row.addWidget(sign_in_button)
        account_row.addWidget(sign_out_button)
        form.addRow("送信アカウント", account_row)
        form.addRow("送信間隔", self.interval)
        form.addRow("履歴の保存期間", self.retention_days)
        save = QPushButton("設定を保存")
        save.setObjectName("primary")
        save.clicked.connect(self.save)
        backup = QPushButton("データをバックアップ")
        backup.clicked.connect(self.backup_data)
        layout.addWidget(box)
        layout.addWidget(QLabel(
            "Entra IDのアプリ登録で「モバイルとデスクトップ アプリ」を構成し、"
            "委任されたアクセス許可 Mail.Send を付与してください。"
        ))
        deliverability = QGroupBox("迷惑メール判定を減らすための管理者設定")
        deliverability_layout = QVBoxLayout(deliverability)
        deliverability_layout.addWidget(QLabel(
            "□ 送信ドメインのSPFを正しく設定する\n"
            "□ Microsoft 365でDKIMを有効化する\n"
            "□ DNSにDMARCレコードを設定する\n"
            "□ 不達・配信不要のアドレスを名簿から削除する\n"
            "□ 広告やニュースレターの大量配信には専用サービスを使用する\n\n"
            "本アプリは1通ずつ2秒以上の間隔で送信し、"
            "重複・不正アドレスを検査します。"
        ))
        layout.addWidget(deliverability)
        layout.addWidget(save, alignment=__import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(backup, alignment=__import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.AlignmentFlag.AlignLeft)
        layout.addStretch()
        self.load()

    def load(self):
        settings = self.storage.settings()
        self.tenant.setText(settings.get("tenant_id", ""))
        self.client.setText(settings.get("client_id", ""))
        self.from_address.setText(settings.get("from_address", ""))
        self.test_address.setText(settings.get("test_address", ""))
        self.refresh_accounts(settings.get("account_username", ""))
        self.interval.setValue(max(int(settings.get("interval_ms", 2000)), 2000))
        self.retention_days.setValue(int(settings.get("retention_days", 365)))

    def current_graph_config(self) -> dict:
        return {
            "tenant_id": self.tenant.text().strip(),
            "client_id": self.client.text().strip(),
            "from_address": self.from_address.text().strip(),
            "account_username": self.account.currentText().strip(),
        }

    def refresh_accounts(self, preferred: str = ""):
        self.account.clear()
        config = {
            "tenant_id": self.tenant.text().strip(),
            "client_id": self.client.text().strip(),
        }
        if config["tenant_id"] and config["client_id"]:
            try:
                self.account.addItems(get_cached_accounts(config))
            except Exception:
                pass
        if preferred and self.account.findText(preferred) < 0:
            self.account.addItem(preferred)
        if preferred:
            self.account.setCurrentText(preferred)

    def authenticate_account(self):
        config = self.current_graph_config()
        try:
            _token, username = get_access_token(config, force_interactive=True)
        except Exception as exc:
            QMessageBox.critical(self, "Microsoft 365認証", str(exc))
            return
        self.refresh_accounts(username)
        self.storage.save_settings({"account_username": username})
        QMessageBox.information(
            self, "Microsoft 365認証",
            f"送信アカウントを確認しました。\n\n{username}")

    def logout_account(self):
        config = self.current_graph_config()
        try:
            sign_out(config)
        except Exception as exc:
            QMessageBox.critical(self, "サインアウト", str(exc))
            return
        self.account.clear()
        self.storage.save_settings({"account_username": ""})
        QMessageBox.information(self, "サインアウト", "認証情報を削除しました。")

    def save(self):
        if not self.tenant.text().strip() or not self.client.text().strip():
            QMessageBox.warning(
                self, "入力エラー", "テナントIDとクライアントIDを入力してください。")
            return
        for value, name in ((self.from_address.text(), "代理差出人"),
                            (self.test_address.text(), "テスト送信先")):
            if value.strip() and not is_valid_email(value):
                QMessageBox.warning(self, "入力エラー", f"{name}の形式が不正です。")
                return
        self.storage.save_settings({
            "tenant_id": self.tenant.text().strip(),
            "client_id": self.client.text().strip(),
            "from_address": self.from_address.text().strip(),
            "test_address": self.test_address.text().strip(),
            "account_username": self.account.currentText().strip(),
            "interval_ms": self.interval.value(),
            "retention_days": self.retention_days.value(),
        })
        QMessageBox.information(self, "設定", "設定を保存しました。")

    def backup_data(self):
        default_name = f"SashikomiMail-backup-{__import__('datetime').date.today():%Y%m%d}.db"
        path, _ = QFileDialog.getSaveFileName(
            self, "バックアップ先", default_name, "データベース (*.db)")
        if not path:
            return
        try:
            shutil.copy2(self.storage.path, path)
        except Exception as exc:
            QMessageBox.critical(self, "バックアップ", str(exc))
            return
        QMessageBox.information(self, "バックアップ", f"保存しました。\n{path}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"差し込みメール送信  v{__version__}")
        icon_path = Path(__file__).resolve().parent.parent / "assets" / "icon.ico"
        self.setWindowIcon(QIcon(str(icon_path)))
        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(
            min(1400, int(screen.width() * 0.95)),
            min(900, int(screen.height() * 0.90)),
        )
        self.setMinimumSize(960, 620)
        QApplication.instance().setStyleSheet(APP_STYLE)
        self.storage = Storage()
        retention_days = int(self.storage.settings().get("retention_days", 365))
        self.storage.delete_old_jobs(retention_days)
        self.tabs = QTabWidget()
        self.compose = ComposeTab(self.storage)
        self.templates = TemplateTab(self.storage)
        self.history = HistoryTab(self.storage)
        self.signatures = SignatureTab(self.storage)
        self.settings = SettingsTab(self.storage)
        self.tabs.addTab(self.compose, "作成・送信")
        self.tabs.addTab(self.templates, "テンプレート")
        self.tabs.addTab(self.signatures, "署名")
        self.tabs.addTab(self.history, "送信履歴")
        self.tabs.addTab(self.settings, "設定")
        self.tabs.currentChanged.connect(self.refresh_current)
        self.compose.history_changed.connect(self.history.refresh)
        self.compose.sending_state_changed.connect(self.set_sending_state)
        self.templates.changed.connect(self.compose.refresh_templates)
        self.signatures.changed.connect(self.compose.refresh_signatures)
        self.history.resend_requested.connect(self.resend_from_history)
        self.setCentralWidget(self.tabs)
        self.statusBar().showMessage("ExcelまたはCSVを選択してください")

    def refresh_current(self, index: int):
        if self.tabs.widget(index) is self.templates:
            self.templates.refresh()
        elif self.tabs.widget(index) is self.signatures:
            self.signatures.refresh()
        elif self.tabs.widget(index) is self.history:
            self.history.refresh()

    def set_sending_state(self, sending: bool):
        for index in range(1, self.tabs.count()):
            self.tabs.setTabEnabled(index, not sending)

    def resend_from_history(self, messages: list[dict]):
        self.tabs.setCurrentWidget(self.compose)
        self.compose.resend_messages(messages)

    def closeEvent(self, event):
        if self.compose.worker and self.compose.worker.isRunning():
            QMessageBox.warning(self, "送信中", "送信を中止して完了を待ってから閉じてください。")
            event.ignore()
            return
        event.accept()
