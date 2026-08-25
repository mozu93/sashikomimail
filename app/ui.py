from __future__ import annotations

import shutil
import threading
import webbrowser
from datetime import date
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QDesktopServices, QIcon
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDialog, QFileDialog, QFormLayout,
    QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QInputDialog, QMainWindow, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QScrollArea, QSpinBox,
    QSplitter, QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.core import (
    carrier_domain_counts, export_recipient_file, is_valid_email,
    load_recipient_file, match_individual_attachments, render_template,
    split_addresses, typo_domain_suspects, unknown_tags, validate_rows,
)
from app.gmail_smtp import GMAIL_ATTACHMENT_LIMIT, open_gmail_connection, send_mail_gmail
from app.graph import (
    ATTACHMENT_LIMIT, get_access_token, get_cached_accounts, send_mail, sign_out,
)
from app.storage import Storage
from app.updater import (
    GITHUB_RELEASES_URL, check_latest_version, download_installer,
    is_newer_version, launch_installer,
)
from app.version import __version__

# 背景色を指定した箇所には必ず文字色も指定する。
# 文字色を省略するとOSのダークモード時にパレット由来の白文字が使われ、
# 白背景に白文字となって読めなくなる。
APP_STYLE = """
QWidget { color: #1f2937; }
QMainWindow, QDialog { background: #f4f7fb; }
QGroupBox { font-weight: bold; border: 1px solid #cbd5e1; border-radius: 7px;
            margin-top: 10px; padding-top: 12px; background: white; color: #1f2937; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #174a78; }
QPushButton { min-height: 28px; padding: 2px 12px; border-radius: 5px;
              border: 1px solid #9ca3af; background: #fff; color: #1f2937; }
QPushButton:hover { background: #edf4fb; }
QPushButton:disabled { background: #f1f3f5; color: #9ca3af; border-color: #cbd5e1; }
QPushButton#primary { color: white; background: #1769aa; border-color: #1769aa; font-weight: bold; }
QPushButton#danger { color: white; background: #b42318; border-color: #b42318; }
/* ID指定は :disabled より優先されるため、無効時の配色もID付きで指定する。
   省略すると送信中でも一括送信ボタンが青いままとなり、
   押せない状態なのか見分けがつかなくなる。 */
QPushButton#primary:disabled, QPushButton#danger:disabled {
    background: #f1f3f5; color: #9ca3af; border-color: #cbd5e1; }
QLineEdit, QComboBox, QPlainTextEdit, QSpinBox { border: 1px solid #aab4c0; border-radius: 4px;
                                               padding: 4px; background: white; color: #1f2937; }
QLineEdit:disabled, QComboBox:disabled, QPlainTextEdit:disabled, QSpinBox:disabled {
    background: #f1f3f5; color: #9ca3af; }
QComboBox QAbstractItemView { background: white; color: #1f2937;
                              border: 1px solid #aab4c0; outline: 0;
                              selection-background-color: #1769aa; selection-color: white; }
QListWidget, QListView, QTableWidget, QTableView, QTreeView {
    background: white; color: #1f2937; alternate-background-color: #f7fafd;
    selection-background-color: #1769aa; selection-color: white; }
QPlainTextEdit { selection-background-color: #1769aa; selection-color: white; }
QTabWidget::pane { border: 1px solid #cbd5e1; background: white; }
QScrollArea { background: #f4f7fb; border: 0; }
QScrollArea > QWidget > QWidget { background: #f4f7fb; }
QScrollBar:vertical, QScrollBar:horizontal { background: #eef2f7; border: 0; }
QScrollBar::handle { background: #b6c2cf; border-radius: 4px; min-height: 24px;
                     min-width: 24px; }
QScrollBar::handle:hover { background: #94a3b8; }
QScrollBar::add-line, QScrollBar::sub-line { background: none; border: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }
QSplitter::handle { background: #dbe4ee; }
QTabBar::tab { min-width: 120px; padding: 9px 16px; background: #e6edf5; color: #1f2937; }
QTabBar::tab:selected { background: white; color: #174a78; font-weight: bold; }
QTabBar::tab:disabled { color: #9ca3af; }
QHeaderView::section { background: #dce9f5; color: #174a78; padding: 5px; border: 0;
                       border-right: 1px solid #c4d3e0; }
QProgressBar { border: 1px solid #aab4c0; border-radius: 4px; background: white;
               color: #1f2937; text-align: center; }
QProgressBar::chunk { background: #9dc4e4; }
QMenu { background: white; color: #1f2937; border: 1px solid #aab4c0; }
QMenu::item:selected { background: #1769aa; color: white; }
QToolTip { background: #ffffe1; color: #1f2937; border: 1px solid #9ca3af; }
"""


class NoWheelComboBox(QComboBox):
    """マウスホイールでは選択が変わらないコンボボックス。

    スクロール領域の中にあるため、画面を読むためのスクロールで
    選択値が入れ替わり、誤入力につながる。ホイールイベントは
    無視して親へ渡し、コンボボックスの上でも画面側がスクロールする。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        # 既定のWheelFocusだと、ホイールだけでフォーカスが移ってしまう。
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event):
        event.ignore()


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

    @staticmethod
    def describe(message: dict) -> str:
        """進捗表示用に、宛先と添付ファイル名を1行にまとめる。"""
        names = "、".join(
            Path(path).name for path in message.get("attachment_paths", []))
        return f"宛先: {message['to_value']}　／　添付: {names or 'なし'}"

    def run(self):
        success = error = consecutive = 0
        provider = self.config.get("provider", "m365")
        connection = None
        try:
            if provider == "gmail":
                connection = open_gmail_connection(self.config)
            else:
                token, _username = get_access_token(self.config)
            for index, message in enumerate(self.messages, 1):
                if self.cancelled:
                    break
                detail = self.describe(message)
                total = len(self.messages)
                self.progress.emit(
                    index - 1, total, f"{index}/{total}件目を送信中　{detail}")
                try:
                    mail_message = {
                        key: value for key, value in message.items()
                        if key != "row_number"
                    }
                    if provider == "gmail":
                        send_mail_gmail(self.config, connection, **mail_message)
                    else:
                        send_mail(self.config, token, **mail_message)
                    success += 1
                    consecutive = 0
                    result = (
                        "Gmailへ送信完了" if provider == "gmail"
                        else "送信要求を受理")
                    self.logged.emit(message["row_number"], message["to_value"],
                                     message["subject"], "成功", "")
                except Exception as exc:
                    error += 1
                    consecutive += 1
                    result = "エラー"
                    self.logged.emit(message["row_number"], message["to_value"],
                                     message["subject"], "エラー", str(exc))
                    if consecutive >= 5:
                        self.failed.emit("5件連続で送信に失敗したため、安全のため中断しました。")
                        self.cancelled = True
                        break
                self.progress.emit(
                    index, total, f"{index}/{total}件 {result}　{detail}")
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
        finally:
            if connection is not None:
                try:
                    connection.quit()
                except Exception:
                    pass
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


class AttachmentContentsDialog(QDialog):
    """送信予定の添付を既定アプリで開き、内容を確認する画面。"""

    def __init__(self, parent, attachment_paths: list[str]):
        super().__init__(parent)
        self.setWindowTitle("添付内容を確認")
        self.resize(640, 360)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "送信予定の添付ファイルです。選択して「開く」を押すと、"
            "既定のアプリで内容を確認できます。"))
        self.files = QListWidget()
        for path in attachment_paths:
            file_path = Path(path)
            size = (
                f"（{file_path.stat().st_size / (1024 * 1024):.2f} MB）"
                if file_path.is_file() else "（見つかりません）")
            item = QListWidgetItem(f"{file_path.name} {size}")
            item.setToolTip(str(file_path))
            item.setData(Qt.ItemDataRole.UserRole, str(file_path))
            self.files.addItem(item)
        self.files.itemDoubleClicked.connect(lambda _item: self.open_selected())
        layout.addWidget(self.files, 1)
        buttons = QHBoxLayout()
        open_button = QPushButton("選択したファイルを開く")
        open_button.setObjectName("primary")
        open_button.clicked.connect(self.open_selected)
        close = QPushButton("閉じる")
        close.clicked.connect(self.accept)
        buttons.addWidget(open_button)
        buttons.addStretch()
        buttons.addWidget(close)
        layout.addLayout(buttons)

    def open_selected(self):
        item = self.files.currentItem()
        if not item:
            QMessageBox.information(self, "添付内容を確認", "開くファイルを選択してください。")
            return
        path = Path(item.data(Qt.ItemDataRole.UserRole))
        if not path.is_file():
            QMessageBox.warning(self, "添付内容を確認", f"ファイルが見つかりません。\n{path}")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            QMessageBox.warning(
                self, "添付内容を確認",
                "ファイルを開けませんでした。対応するアプリがインストールされているか確認してください。")


class ConditionalTagDialog(QDialog):
    def __init__(self, parent, headers: list[str]):
        super().__init__(parent)
        self.setWindowTitle("条件付きタグを挿入")
        self.resize(420, 260)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "選んだ列にデータがある行だけ、前後に付けた文字も含めて挿入します。\n"
            "データが空欄の行では、タグも前後の文字も何も表示されません。"))
        form = QFormLayout()
        self.column = NoWheelComboBox()
        self.column.addItems(headers)
        self.prefix = QLineEdit()
        self.prefix.setPlaceholderText("例：、（省略可）")
        self.suffix = QLineEdit()
        self.suffix.setPlaceholderText("例： 様（省略可）")
        form.addRow("差し込む列", self.column)
        form.addRow("前に付ける文字", self.prefix)
        form.addRow("後に付ける文字", self.suffix)
        layout.addLayout(form)
        buttons = QHBoxLayout()
        ok = QPushButton("挿入")
        ok.setObjectName("primary")
        ok.clicked.connect(self.accept)
        cancel = QPushButton("キャンセル")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(ok)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

    def tag_text(self) -> str:
        column = self.column.currentText()
        prefix = self.prefix.text()
        suffix = self.suffix.text()
        if not prefix and not suffix:
            return f"{{{column}}}"
        return f"{{{prefix}|{column}|{suffix}}}"


class ContactPickerDialog(QDialog):
    def __init__(self, parent, contacts: list[dict]):
        super().__init__(parent)
        self.setWindowTitle("連絡先から追加")
        self.resize(420, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("追加する連絡先にチェックを付けてください。"))
        self.list = QListWidget()
        for contact in contacts:
            item = QListWidgetItem(f"{contact['name']}（{contact['email']}）")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(256, contact)
            self.list.addItem(item)
        layout.addWidget(self.list, 1)
        buttons = QHBoxLayout()
        ok = QPushButton("追加")
        ok.setObjectName("primary")
        ok.clicked.connect(self.accept)
        cancel = QPushButton("キャンセル")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(ok)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

    def selected_emails(self) -> list[str]:
        emails = []
        for index in range(self.list.count()):
            item = self.list.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                emails.append(item.data(256)["email"])
        return emails


class UpdateBanner(QWidget):
    update_found = pyqtSignal(dict)
    download_progress = pyqtSignal(int, int)
    download_finished = pyqtSignal(object)
    download_failed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.info: dict = {}
        self.installer_path: Path | None = None
        self.setStyleSheet(
            "UpdateBanner { background:#fef9c3; border:1px solid #fde047; }"
            "UpdateBanner QLabel { color:#713f12; }")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 8, 6)
        self.message = QLabel()
        self.action = QPushButton("ダウンロード")
        self.close_button = QPushButton("×")
        self.close_button.setFixedWidth(32)
        layout.addWidget(self.message, 1)
        layout.addWidget(self.action)
        layout.addWidget(self.close_button)
        self.action.clicked.connect(self.start_download)
        self.close_button.clicked.connect(self.hide)
        self.update_found.connect(self.show_update)
        self.download_progress.connect(self.show_progress)
        self.download_finished.connect(self.download_ready)
        self.download_failed.connect(self.show_error)
        self.hide()
        threading.Thread(target=self.check, daemon=True).start()

    def check(self):
        info = check_latest_version()
        if info and is_newer_version(__version__, info["tag_name"]):
            self.update_found.emit(info)

    def show_update(self, info: dict):
        self.info = info
        self.message.setText(
            f"新しいバージョン {info['tag_name']} があります（現在 v{__version__}）")
        if info.get("download_url"):
            self.action.setText("ダウンロード")
            self.action.setEnabled(True)
        else:
            self.action.setText("GitHubで確認")
        self.show()

    def start_download(self):
        url = self.info.get("download_url", "")
        if not url:
            webbrowser.open(self.info.get("html_url", GITHUB_RELEASES_URL))
            return
        self.action.setEnabled(False)
        threading.Thread(target=self._download, args=(url,), daemon=True).start()

    def _download(self, url: str):
        try:
            path = download_installer(
                url,
                lambda received, total: self.download_progress.emit(received, total),
            )
            self.download_finished.emit(path)
        except Exception as exc:
            self.download_failed.emit(str(exc))

    def show_progress(self, received: int, total: int):
        received_mb = received / 1048576
        total_text = f" / {total / 1048576:.1f} MB" if total else " MB"
        self.message.setText(f"アップデートをダウンロード中: {received_mb:.1f}{total_text}")

    def download_ready(self, path: Path):
        self.installer_path = path
        self.message.setText("ダウンロード完了。更新するとアプリを再起動します。")
        self.action.setText("今すぐ更新")
        self.action.setEnabled(True)
        self.action.clicked.disconnect()
        self.action.clicked.connect(self.install)

    def show_error(self, detail: str):
        self.message.setText(f"ダウンロードに失敗しました: {detail}")
        self.action.setText("再試行")
        self.action.setEnabled(True)

    def install(self):
        if not self.installer_path:
            return
        try:
            launch_installer(self.installer_path)
        except Exception as exc:
            QMessageBox.warning(self, "アップデート", str(exc))
            return
        QApplication.quit()


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
        self.individual_match_columns: list[str] = []
        self.individual_folder = ""
        self.filtered_indices: list[int] = []
        self.filter_indices: list[int] = []
        self.included_rows: set[int] = set()
        self.active_filter_desc: str = ""
        self.source_path = ""
        self.recipient_display_name = ""
        self._updating_table = False
        # 確認時点のエラー内容を保持する。内容が変われば許可は自動的に無効になる。
        self.approved_validation_issues: dict[int, tuple[str, ...]] = {}
        self.validation_errors: dict[int, list[str]] = {}
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
        export_button = QPushButton("Excelとして出力")
        export_button.setToolTip("セル編集や行削除を反映した現在のデータをExcelファイルに出力します")
        export_button.clicked.connect(self.export_recipient_data)
        source_layout.addWidget(choose, 0, 0)
        source_layout.addWidget(open_list, 0, 1)
        source_layout.addWidget(save_list, 0, 2)
        source_layout.addWidget(delete_list, 0, 3)
        source_layout.addWidget(export_button, 0, 4)
        source_layout.addWidget(self.file_label, 1, 0, 1, 5)
        source_layout.setColumnStretch(5, 1)
        root.addWidget(source)

        splitter = QSplitter()
        left = QWidget()
        left_layout = QVBoxLayout(left)
        preview_box = QGroupBox("2. データプレビュー（セルをクリックして編集）")
        preview_layout = QVBoxLayout(preview_box)
        filter_row = QHBoxLayout()
        self.filter_column = QComboBox()
        self.filter_operator = QComboBox()
        self.filter_operator.addItems(["含む", "完全一致", "空欄", "空欄でない"])
        self.filter_operator.currentTextChanged.connect(self.update_filter_input_state)
        self.filter_value = QLineEdit()
        self.filter_value.setPlaceholderText("絞り込む文字を入力")
        self.filter_value.setClearButtonEnabled(True)
        self.filter_value.returnPressed.connect(self.apply_filter)
        self.filter_value.textChanged.connect(self.on_filter_value_changed)
        apply_filter_button = QPushButton("絞り込み")
        apply_filter_button.clicked.connect(self.apply_filter)
        filter_row.addWidget(QLabel("列"))
        filter_row.addWidget(self.filter_column)
        filter_row.addWidget(self.filter_operator)
        filter_row.addWidget(self.filter_value, 1)
        filter_row.addWidget(apply_filter_button)
        search_row = QHBoxLayout()
        self.search_value = QLineEdit()
        self.search_value.setPlaceholderText("全列から検索（入力するとすぐに反映）")
        self.search_value.setClearButtonEnabled(True)
        self.search_value.textChanged.connect(self.update_visible_rows)
        delete_row_button = QPushButton("選択行を削除")
        delete_row_button.setObjectName("danger")
        delete_row_button.clicked.connect(self.delete_selected_row)
        approve_error_button = QPushButton("選択行のエラーを確認・有効化")
        approve_error_button.setToolTip(
            "エラー内容を確認し、問題がない行だけ送信対象として有効にします")
        approve_error_button.clicked.connect(self.approve_selected_row_errors)
        search_row.addWidget(QLabel("検索"))
        search_row.addWidget(self.search_value, 1)
        search_row.addWidget(approve_error_button)
        search_row.addWidget(delete_row_button)
        self.summary = QLabel("0件")
        self.active_filter_label = QLabel("")
        self.active_filter_label.setStyleSheet("color: #b45309; font-weight: bold;")
        self.active_filter_label.setVisible(False)
        self.table = QTableWidget()
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.SelectedClicked)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.itemChanged.connect(self.on_table_item_changed)
        self.table.horizontalHeader().sectionClicked.connect(self.on_status_header_clicked)
        preview_layout.addLayout(search_row)
        preview_layout.addLayout(filter_row)
        preview_layout.addWidget(self.summary)
        preview_layout.addWidget(self.active_filter_label)
        preview_layout.addWidget(self.table)
        left_layout.addWidget(preview_box)
        splitter.addWidget(left)

        editor = QWidget()
        editor_layout = QVBoxLayout(editor)
        destination = QGroupBox("3. 宛先設定")
        form = QFormLayout(destination)
        self.to_column = NoWheelComboBox()
        self.to_column.currentTextChanged.connect(self.on_validation_columns_changed)
        self.fixed_cc = QLineEdit()
        self.fixed_cc.setPlaceholderText("複数指定は ; または , で区切る")
        fixed_cc_pick = QPushButton("連絡先")
        fixed_cc_pick.setToolTip("「連絡先」タブに登録した連絡先から選んで追記します")
        fixed_cc_pick.clicked.connect(lambda: self.pick_contacts(self.fixed_cc))
        fixed_cc_row = QHBoxLayout()
        fixed_cc_row.addWidget(self.fixed_cc, 1)
        fixed_cc_row.addWidget(fixed_cc_pick)
        self.bcc = QLineEdit()
        self.bcc.setPlaceholderText("複数指定は ; または , で区切る")
        bcc_pick = QPushButton("連絡先")
        bcc_pick.setToolTip("「連絡先」タブに登録した連絡先から選んで追記します")
        bcc_pick.clicked.connect(lambda: self.pick_contacts(self.bcc))
        bcc_row = QHBoxLayout()
        bcc_row.addWidget(self.bcc, 1)
        bcc_row.addWidget(bcc_pick)
        self.sender = NoWheelComboBox()
        self.sender.setToolTip(
            "この送信で使用する差出人を選択します。\n"
            "選んだ差出人に応じて送信方法（Microsoft 365 / Gmail）も切り替わります。\n"
            "（設定タブで事前に接続設定が必要です）")
        self.sender.currentIndexChanged.connect(self.on_sender_changed)
        form.addRow("差出人", self.sender)
        form.addRow("To列（必須）", self.to_column)
        form.addRow("固定CC", fixed_cc_row)
        form.addRow("固定BCC", bcc_row)
        editor_layout.addWidget(destination)

        template = QGroupBox("4. 件名・本文")
        template_layout = QVBoxLayout(template)
        row = QHBoxLayout()
        self.template_combo = NoWheelComboBox()
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
        self.body.setMinimumHeight(225)
        template_layout.addLayout(row)
        template_layout.addWidget(QLabel("件名"))
        template_layout.addWidget(self.subject)
        template_layout.addWidget(QLabel("本文"))
        template_layout.addWidget(self.body)
        signature_row = QHBoxLayout()
        self.signature_combo = NoWheelComboBox()
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
        conditional_button = QPushButton("条件付きタグを挿入（前後に文字を付ける）")
        conditional_button.setToolTip(
            "データが空欄の行では、タグも前後に付けた文字も表示されません。\n"
            "例：「タグA」様、{、|タグB|様} → タグBが空欄なら「タグA様」だけになります")
        conditional_button.clicked.connect(self.insert_conditional_tag)
        tags_layout.addWidget(conditional_button)
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
            "未設定（2列の値を「_」でつないでファイル名と照合します）")
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
        splitter.setSizes([620, 660])
        root.addWidget(splitter, 1)

        self.send_status = QLabel()
        self.send_status.setWordWrap(True)
        self.send_status.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.send_status.setStyleSheet(
            "QLabel { color:#174a78; background:#eef4fb; border:1px solid #cbd5e1;"
            " border-radius:5px; padding:6px 9px; }")
        self.send_status.hide()
        root.addWidget(self.send_status)

        controls = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setFormat("%v / %m件")
        preview = QPushButton("選択行をプレビュー")
        preview.clicked.connect(self.preview_selected)
        attachment_preview = QPushButton("添付内容を確認")
        attachment_preview.clicked.connect(self.preview_attachments_for_selected)
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
        controls.addWidget(attachment_preview)
        controls.addWidget(self.test_button)
        controls.addWidget(self.send_button)
        controls.addWidget(self.cancel_button)
        root.addLayout(controls)
        self.send_lock_widgets = [
            source, splitter, preview, attachment_preview, self.test_button, self.send_button,
        ]
        self.refresh_templates()
        self.refresh_signatures()
        self.refresh_sender_options()

    def refresh_templates(self):
        current = self.template_combo.currentText()
        self.template_combo.clear()
        self.template_combo.addItem("テンプレートなし", None)
        for template in self.storage.templates():
            self.template_combo.addItem(template["name"], template)
        index = self.template_combo.findText(current)
        self.template_combo.setCurrentIndex(index if index >= 0 else 0)

    def refresh_signatures(self):
        current = self.signature_combo.currentText()
        self.signature_combo.clear()
        self.signature_combo.addItem("署名なし", "")
        for signature in self.storage.signatures():
            self.signature_combo.addItem(signature["name"], signature["body"])
        index = self.signature_combo.findText(current)
        self.signature_combo.setCurrentIndex(index if index >= 0 else 0)

    def pick_contacts(self, target: QLineEdit):
        contacts = self.storage.cc_contacts()
        if not contacts:
            QMessageBox.information(
                self, "連絡先から追加",
                "登録された連絡先がありません。「連絡先」タブから登録してください。")
            return
        dialog = ContactPickerDialog(self, contacts)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        emails = dialog.selected_emails()
        if not emails:
            return
        existing = split_addresses(target.text())
        merged = existing + [email for email in emails if email not in existing]
        target.setText("; ".join(merged))

    def current_attachment_limit(self) -> int:
        return (
            GMAIL_ATTACHMENT_LIMIT if self.sender.currentData() == "gmail"
            else ATTACHMENT_LIMIT
        )

    def on_sender_changed(self):
        # Gmailとの切り替えで添付の安全上限が変わるため、表示を作り直す。
        self.refresh_attachment_display()

    def refresh_sender_options(self):
        """設定済みアカウントから、この送信で選べる差出人を更新する。"""
        current_mode = self.sender.currentData()
        settings = self.storage.settings()
        account = settings.get("account_username", "")
        proxy = settings.get("from_address", "")
        gmail_address = settings.get("gmail_address", "")
        # 項目の入れ替え中に添付表示が何度も走らないよう、signalを止める。
        self.sender.blockSignals(True)
        self.sender.clear()
        self.sender.addItem(
            f"自分のアドレス（{account}）" if account else "自分のアドレス",
            "self",
        )
        if proxy:
            self.sender.addItem(f"代理差出人（{proxy}）", "proxy")
        # 未設定でも項目は出す。設定タブへ誘導できるようにするため。
        self.sender.addItem(
            f"Gmail（{gmail_address}）" if gmail_address else "Gmail（未設定）",
            "gmail",
        )
        index = self.sender.findData(current_mode)
        self.sender.setCurrentIndex(index if index >= 0 else 0)
        self.sender.blockSignals(False)
        self.refresh_attachment_display()

    def selected_sender_config(self) -> tuple[dict, str]:
        settings = self.storage.settings()
        if self.sender.currentData() == "gmail":
            settings["provider"] = "gmail"
            gmail_address = settings.get("gmail_address", "")
            settings["from_address"] = gmail_address
            return settings, gmail_address or "未設定"
        settings["provider"] = "m365"
        account = settings.get("account_username", "") or "未確認"
        proxy = settings.get("from_address", "")
        if self.sender.currentData() == "proxy" and proxy:
            settings["from_address"] = proxy
            return settings, proxy
        settings["from_address"] = ""
        return settings, account

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

    def export_recipient_data(self):
        if not self.rows:
            QMessageBox.warning(self, "Excel出力", "先にExcelまたはCSVを読み込んでください。")
            return
        base_name = Path(self.source_path).stem if self.source_path else "名簿"
        default_name = f"{base_name}_{date.today().strftime('%Y%m%d')}.xlsx"
        path, _ = QFileDialog.getSaveFileName(
            self, "Excelとして出力", default_name, "Excel ファイル (*.xlsx)")
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        try:
            export_recipient_file(path, self.headers, self.rows)
        except Exception as exc:
            QMessageBox.critical(self, "出力エラー", str(exc))
            return
        QMessageBox.information(self, "Excel出力", f"「{Path(path).name}」に出力しました。")

    def apply_recipient_data(self, source_path: str, headers: list[str],
                             rows: list[dict[str, str]], display_name: str = ""):
        self.source_path, self.headers, self.rows = source_path, headers, rows
        self.approved_validation_issues.clear()
        self.validation_errors.clear()
        self.clear_individual_attachments()
        self.filtered_indices = list(range(len(rows)))
        self.filter_indices = list(range(len(rows)))
        self.included_rows = set(range(len(rows)))
        self.active_filter_desc = ""
        source_name = display_name or Path(source_path).name
        self.recipient_display_name = source_name
        self.file_label.setText(f"{source_name}（{len(self.rows)}件）")
        self.to_column.clear()
        self.to_column.addItems(self.headers)
        self.filter_column.clear()
        self.filter_column.addItems(self.headers)
        self.filter_value.clear()
        self.search_value.blockSignals(True)
        self.search_value.clear()
        self.search_value.blockSignals(False)
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

    def on_status_header_clicked(self, section: int):
        if section != 0 or not self.rows:
            return
        all_included = len(self.included_rows) == len(self.rows)
        self.included_rows = set() if all_included else set(range(len(self.rows)))
        self.refresh_validation()

    def render_table(self):
        self._updating_table = True
        self.table.setColumnCount(len(self.headers) + 1)
        self.table.setHorizontalHeaderLabels(["状態（送信対象）"] + self.headers)
        self.table.horizontalHeaderItem(0).setToolTip(
            "チェックを外した行は送信対象から除外されます。\n"
            "見出しをクリックすると全行のチェックを一括切替できます。")
        self.table.setRowCount(len(self.rows))
        for r, row in enumerate(self.rows):
            status_item = QTableWidgetItem("")
            status_item.setFlags(
                (status_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                | Qt.ItemFlag.ItemIsUserCheckable)
            status_item.setCheckState(
                Qt.CheckState.Checked if r in self.included_rows
                else Qt.CheckState.Unchecked)
            self.table.setItem(r, 0, status_item)
            for c, header in enumerate(self.headers, 1):
                self.table.setItem(r, c, QTableWidgetItem(row.get(header, "")))
        self._updating_table = False
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        if self.rows:
            self.table.selectRow(0)
        self.refresh_validation()

    def update_active_filter_label(self):
        parts = []
        if self.active_filter_desc:
            parts.append(f"絞り込み中: {self.active_filter_desc}")
        search_text = self.search_value.text().strip()
        if search_text:
            parts.append(f"検索中: 「{search_text}」")
        self.active_filter_label.setText(" ／ ".join(parts))
        self.active_filter_label.setVisible(bool(parts))

    def refresh_validation(self):
        self.update_active_filter_label()
        if not self.rows or not self.to_column.currentText():
            self.summary.setText(f"表示 0件 / 送信対象 0件 / 全{len(self.rows)}件")
            return
        indices = self.filtered_indices or []
        target_rows = [self.rows[index] for index in indices]
        subset_errors = validate_rows(
            target_rows, self.to_column.currentText(),
            row_numbers=[index + 2 for index in indices])
        errors = {indices[index]: value for index, value in subset_errors.items()}
        self.validation_errors = errors
        self._updating_table = True
        for row_index in range(len(self.rows)):
            item = self.table.item(row_index, 0)
            if not item:
                continue
            included = row_index in self.included_rows
            item.setCheckState(
                Qt.CheckState.Checked if included else Qt.CheckState.Unchecked)
            if row_index in errors:
                error_detail = "\n".join(errors[row_index])
                approved = (
                    self.approved_validation_issues.get(row_index)
                    == tuple(errors[row_index])
                )
                item.setText("確認済み" if approved else "エラー")
                tooltip = ("確認済み（送信対象）\n" if approved else "") + error_detail
                base_color = "#fef3c7" if approved else "#fee2e2"
            else:
                item.setText("OK")
                tooltip = ""
                base_color = "white"
            if not included:
                tooltip = "送信対象外（チェックがオフ）\n" + tooltip if tooltip else "送信対象外（チェックがオフ）"
                base_color = "#e2e8f0"
            for column in range(self.table.columnCount()):
                cell = self.table.item(row_index, column)
                cell.setToolTip(tooltip)
                cell.setBackground(QColor(base_color))
                cell.setForeground(QColor("#1f2937"))
        self._updating_table = False
        target_count = sum(1 for i in indices if i in self.included_rows)
        self.summary.setText(
            f"表示 {len(indices)}件 / 送信対象 {target_count}件 / 全{len(self.rows)}件"
            f"（未確認エラー "
            f"{sum(self.approved_validation_issues.get(i) != tuple(v) for i, v in errors.items())}件"
            f" / 確認済み "
            f"{sum(self.approved_validation_issues.get(i) == tuple(v) for i, v in errors.items())}件）")

    def on_validation_columns_changed(self, _text: str = ""):
        self.approved_validation_issues.clear()
        self.refresh_validation()

    def approve_selected_row_errors(self):
        row_index = self.table.currentRow()
        if row_index < 0 or row_index >= len(self.rows):
            QMessageBox.information(
                self, "エラーの確認", "確認する行を選択してください。")
            return
        issues = self.validation_errors.get(row_index, [])
        if not issues:
            QMessageBox.information(
                self, "エラーの確認", "選択した行にエラーはありません。")
            return
        if self.approved_validation_issues.get(row_index) == tuple(issues):
            QMessageBox.information(
                self, "エラーの確認", "選択した行はすでに確認済みです。")
            return
        row_preview = "\n".join(
            f"{header}: {self.rows[row_index].get(header, '')}"
            for header in self.headers
            if self.rows[row_index].get(header, "")
        )
        answer = QMessageBox.question(
            self, "エラー行を有効化",
            f"Excel / CSVの{row_index + 2}行目\n\n"
            f"【エラー内容】\n・" + "\n・".join(issues)
            + f"\n\n【行の内容】\n{row_preview or '（値なし）'}"
            + "\n\n内容を確認し、問題がない場合だけ有効化してください。"
              "\nこの行を送信対象にしますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.approved_validation_issues[row_index] = tuple(issues)
            self.refresh_validation()

    def on_table_item_changed(self, item: QTableWidgetItem):
        if self._updating_table:
            return
        if item.column() == 0:
            row_index = item.row()
            if row_index >= len(self.rows):
                return
            if item.checkState() == Qt.CheckState.Checked:
                self.included_rows.add(row_index)
            else:
                self.included_rows.discard(row_index)
            self.refresh_validation()
            return
        row_index = item.row()
        column_index = item.column() - 1
        if row_index >= len(self.rows) or column_index >= len(self.headers):
            return
        header = self.headers[column_index]
        self.rows[row_index][header] = item.text().strip()
        self.approved_validation_issues.pop(row_index, None)
        if header in self.individual_match_columns:
            self.clear_individual_attachments()
        if self.search_value.text().strip():
            self.update_visible_rows()
        else:
            self.refresh_validation()

    def delete_selected_row(self):
        row_index = self.table.currentRow()
        if row_index < 0 or row_index >= len(self.rows):
            QMessageBox.information(self, "行の削除", "削除する行を選択してください。")
            return
        preview = " / ".join(
            value for value in self.rows[row_index].values() if value)[:100]
        if QMessageBox.question(
                self, "行の削除",
                f"選択した行を名簿から削除しますか？\n\n{preview}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.rows.pop(row_index)
        self.approved_validation_issues.clear()
        self.validation_errors.clear()
        self.filter_indices = list(range(len(self.rows)))
        self.filtered_indices = list(range(len(self.rows)))
        self.included_rows = {
            i if i < row_index else i - 1
            for i in self.included_rows if i != row_index
        }
        self.file_label.setText(
            f"{self.recipient_display_name}（{len(self.rows)}件）")
        self.clear_individual_attachments()
        self.render_table()
        self.apply_filter(silent=True)

    def update_filter_input_state(self):
        needs_value = self.filter_operator.currentText() in ("含む", "完全一致")
        self.filter_value.setEnabled(needs_value)

    def apply_filter(self, _checked: bool = False, silent: bool = False):
        if not self.rows or not self.filter_column.currentText():
            return
        column = self.filter_column.currentText()
        operator = self.filter_operator.currentText()
        needle = self.filter_value.text().strip().casefold()
        if operator in ("含む", "完全一致") and not needle:
            if silent:
                self.filter_indices = list(range(len(self.rows)))
                self.active_filter_desc = ""
                self.update_visible_rows()
                return
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
            if include:
                matched.append(index)
        self.filter_indices = matched
        if operator in ("含む", "完全一致"):
            self.active_filter_desc = f"「{column}」が「{self.filter_value.text().strip()}」を{operator}"
        else:
            self.active_filter_desc = f"「{column}」が{operator}"
        self.update_visible_rows()

    def clear_filter(self):
        self.filter_indices = list(range(len(self.rows)))
        self.active_filter_desc = ""
        self.filter_value.clear()
        self.update_visible_rows()

    def on_filter_value_changed(self, text: str):
        if not text.strip() and self.filter_indices != list(range(len(self.rows))):
            self.clear_filter()

    def update_visible_rows(self, _text: str = ""):
        needle = self.search_value.text().strip().casefold()
        base = set(self.filter_indices)
        matched = []
        for index, row in enumerate(self.rows):
            search_match = not needle or any(
                needle in value.casefold() for value in row.values())
            include = index in base and search_match
            self.table.setRowHidden(index, not include)
            if include:
                matched.append(index)
        self.filtered_indices = matched
        if matched:
            self.table.selectRow(matched[0])
        else:
            self.table.clearSelection()
        self.refresh_validation()

    def load_template(self):
        data = self.template_combo.currentData()
        if data:
            self.subject.setText(data["subject"])
            self.body.setPlainText(data["body"])

    def insert_conditional_tag(self):
        if not self.headers:
            QMessageBox.information(
                self, "条件付きタグ", "先にExcelまたはCSVを読み込んでください。")
            return
        dialog = ConditionalTagDialog(self, self.headers)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.body.insertPlainText(dialog.tag_text())

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
        limit = self.current_attachment_limit()
        rejected = []
        for path in paths:
            if path not in self.attachments:
                proposed_size = self.attachment_total_size() + Path(path).stat().st_size
                if proposed_size > limit:
                    rejected.append(Path(path).name)
                else:
                    self.attachments.append(path)
        self.refresh_attachment_display()
        if rejected:
            QMessageBox.warning(
                self, "添付容量の上限",
                f"安全上限の合計{limit / (1024 * 1024):.1f}MBを超えるため、"
                "次のファイルは追加しませんでした。\n\n"
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
        limit = self.current_attachment_limit()
        total = self.attachment_total_size()
        count = len(self.attachments)
        mb = total / (1024 * 1024)
        remaining = max(limit - total, 0) / (1024 * 1024)
        self.attach_label.setText(
            "、".join(Path(path).name for path in self.attachments) or "添付なし")
        self.attach_label.setToolTip("\n".join(
            f"{Path(path).name}  ({Path(path).stat().st_size / (1024 * 1024):.2f} MB)"
            for path in self.attachments if Path(path).is_file()
        ))
        self.attach_usage.setRange(0, limit)
        self.attach_usage.setValue(min(total, limit))
        self.attach_usage.setFormat(
            f"{count}点 / {mb:.2f} MB（残り {remaining:.2f} MB・点数上限なし）")

    def set_individual_attachments(self):
        if not self.rows:
            QMessageBox.warning(
                self, "個別添付", "先にExcel、CSVまたは保存済み名簿を開いてください。")
            return
        first_column, ok = QInputDialog.getItem(
            self, "個別添付の照合列（1/2）", "ファイル名の1項目目:",
            self.headers, 0, False)
        if not ok:
            return
        second_headers = [header for header in self.headers if header != first_column]
        if not second_headers:
            QMessageBox.warning(self, "個別添付", "照合には異なる2列が必要です。")
            return
        second_column, ok = QInputDialog.getItem(
            self, "個別添付の照合列（2/2）",
            f"「{first_column}_」に続く2項目目:",
            second_headers, 0, False)
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
            self.rows, [first_column, second_column], file_paths)
        self.individual_attachments = mapping
        self.individual_match_columns = [first_column, second_column]
        self.individual_folder = folder
        file_count = sum(len(paths) for paths in mapping.values())
        self.individual_label.setText(
            f"{first_column}_{second_column}で照合："
            f"{len(mapping)}/{len(self.rows)}件に"
            f"{file_count}ファイルを割当")
        self.individual_label.setToolTip(
            f"フォルダ: {folder}\n未一致ファイル: {len(unmatched)}件")
        details = (
            f"{len(self.rows)}件中 {len(mapping)}件へ、"
            f"合計{file_count}ファイルを割り当てました。\n"
            f"照合形式: {first_column}_{second_column}\n\n割当一覧:\n"
        )
        assignments = []
        for index, paths in sorted(mapping.items()):
            key = "_".join(
                self.rows[index].get(column, "").strip()
                for column in self.individual_match_columns)
            assignments.append(
                f"{index + 2}行目 {key}: "
                + "、".join(Path(path).name for path in paths))
        details += "\n".join(assignments[:20]) or "（割当なし）"
        if len(assignments) > 20:
            details += f"\nほか {len(assignments) - 20}件"
        if unmatched:
            preview = "\n".join(Path(path).name for path in unmatched[:10])
            details += f"\n\n一致しなかったファイル: {len(unmatched)}件\n{preview}"
            if len(unmatched) > 10:
                details += f"\nほか {len(unmatched) - 10}件"
        QMessageBox.information(self, "個別添付の照合結果", details)

    def clear_individual_attachments(self):
        self.individual_attachments = {}
        self.individual_match_columns = []
        self.individual_folder = ""
        if hasattr(self, "individual_label"):
            self.individual_label.setText(
                "未設定（2列の値を「_」でつないでファイル名と照合します）")
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
        cc_value = "" if test_to else self.fixed_cc.text().strip()
        return {
            "row_number": index + 2,
            "organization_name": row.get("事業所名", "").strip(),
            "to_value": to_value,
            "cc_value": cc_value,
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

    def preview_attachments_for_selected(self):
        """選択行に実際に付く共通・個別添付の内容確認を開く。"""
        selected = self.current_row()
        if not selected:
            QMessageBox.information(
                self, "添付内容を確認", "確認する宛先の行を選択してください。")
            return
        message = self.message_for(*selected)
        if not message["attachment_paths"]:
            QMessageBox.information(
                self, "添付内容を確認", "この宛先に送る添付ファイルはありません。")
            return
        AttachmentContentsDialog(self, message["attachment_paths"]).exec()

    def confirm_delivery_risks(self, target_rows: list[dict[str, str]]) -> bool:
        """打ち間違いドメインと携帯キャリア宛を送信前に確認する。

        どちらも形式は正しいためエラー検査では検出できず、
        誤送信・不達になっても送信側にエラーが返らない。
        """
        addresses = []
        column = self.to_column.currentText()
        if column:
            for row in target_rows:
                addresses.extend(split_addresses(row.get(column, "")))
        suspects = typo_domain_suspects(addresses)
        carriers = carrier_domain_counts(addresses)
        if not suspects and not carriers:
            return True
        sections = []
        if suspects:
            listed = "\n".join(
                f"　・{address}　→　{correction} の打ち間違いでは？"
                for address, correction in suspects[:10])
            more = (
                f"\n　ほか {len(suspects) - 10}件"
                if len(suspects) > 10 else ""
            )
            sections.append(
                f"■ 打ち間違いが疑われるドメイン（{len(suspects)}件）\n"
                f"{listed}{more}\n"
                "　これらは第三者が実際に運用しているドメインの場合があり、\n"
                "　誤送信しても配信不能通知が返りません。\n"
                "　添付ファイルごと他人に届いたままになります。")
        if carriers:
            _settings, from_address = self.selected_sender_config()
            sender_domain = (
                from_address.rpartition("@")[2] if "@" in from_address
                else "送信元"
            )
            listed = "、".join(
                f"{domain} {count}件" for domain, count in carriers.items())
            sections.append(
                f"■ 携帯キャリア宛（合計{sum(carriers.values())}件）\n"
                f"　{listed}\n"
                "　「なりすまし規制」「パソコンメール拒否」が有効な回線では、\n"
                "　送信側にエラーを返さないまま破棄されることがあります。\n"
                f"　事前に {sender_domain} の指定受信設定をご案内ください。")
        return QMessageBox.question(
            self, "宛先の注意点を確認",
            "\n\n".join(sections) + "\n\nこのまま送信準備を続けますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def preflight(self) -> list[dict] | None:
        if not self.rows:
            QMessageBox.warning(self, "確認", "宛先データを読み込んでください。")
            return None
        target_indices = [
            index for index in self.filtered_indices if index in self.included_rows
        ]
        if not target_indices:
            QMessageBox.warning(self, "確認", "送信対象が0件です。チェックボックスをご確認ください。")
            return None
        target_rows = [self.rows[index] for index in target_indices]
        errors = validate_rows(
            target_rows, self.to_column.currentText(),
            row_numbers=[index + 2 for index in target_indices])
        unapproved_errors = {
            target_indices[index]: issues
            for index, issues in errors.items()
            if self.approved_validation_issues.get(target_indices[index])
            != tuple(issues)
        }
        if unapproved_errors:
            preview = "\n".join(
                f"{index + 2}行目: {'、'.join(issues)}"
                for index, issues in list(unapproved_errors.items())[:10]
            )
            more = (
                f"\nほか {len(unapproved_errors) - 10}件"
                if len(unapproved_errors) > 10 else ""
            )
            QMessageBox.warning(
                self, "確認",
                f"未確認のエラー行が{len(unapproved_errors)}件あります。\n\n"
                f"{preview}{more}\n\n"
                "データプレビューで行を選び、"
                "「選択行のエラーを確認・有効化」から確認してください。")
            return None
        bad_cc = [x for x in split_addresses(self.fixed_cc.text()) if not is_valid_email(x)]
        if bad_cc:
            QMessageBox.warning(self, "確認", "固定CCの形式が不正です。")
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
        limit = self.current_attachment_limit()
        oversized_rows = []
        for index in target_indices:
            paths = self.attachments + self.individual_attachments.get(index, [])
            size = sum(Path(path).stat().st_size for path in paths)
            if size > limit:
                oversized_rows.append(
                    f"{index + 2}行目: {size / (1024 * 1024):.2f} MB")
        if oversized_rows:
            QMessageBox.warning(
                self, "添付容量の上限",
                f"共通添付と個別添付の合計が安全上限の{limit / (1024 * 1024):.1f}MBを"
                "超える行があります。\n\n"
                + "\n".join(oversized_rows[:10]))
            return None
        if not self.confirm_delivery_risks(target_rows):
            return None
        if self.individual_match_columns:
            unassigned = [
                index for index in target_indices
                if not self.individual_attachments.get(index)
            ]
            if unassigned:
                preview = []
                for index in unassigned[:10]:
                    key = "_".join(
                        self.rows[index].get(column, "").strip()
                        for column in self.individual_match_columns)
                    preview.append(
                        f"{index + 2}行目: {key or '（照合値が空欄）'}")
                more = (
                    f"\nほか {len(unassigned) - 10}件"
                    if len(unassigned) > 10 else ""
                )
                answer = QMessageBox.question(
                    self, "個別添付の未割当を確認",
                    f"送信対象{len(target_indices)}件のうち、"
                    f"{len(unassigned)}件に個別添付がありません。\n\n"
                    + "\n".join(preview) + more
                    + "\n\n個別添付なしのまま送信準備を続けますか？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No)
                if answer != QMessageBox.StandardButton.Yes:
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
        settings, _from_address = self.selected_sender_config()
        if settings.get("provider") == "gmail" and not (
                settings.get("gmail_address") and settings.get("gmail_app_password")):
            QMessageBox.warning(
                self, "Gmail未設定",
                "設定タブでGmailアドレスとアプリパスワードを登録してから"
                "テスト送信してください。")
            return
        address = (
            settings.get("gmail_test_address", "") if settings.get("provider") == "gmail"
            else settings.get("test_address", "")
        )
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
        settings, from_address = self.selected_sender_config()
        if settings.get("provider") == "gmail":
            if not settings.get("gmail_address") or not settings.get("gmail_app_password"):
                QMessageBox.warning(
                    self, "Gmail未設定",
                    "設定タブでGmailアドレスとアプリパスワードを登録してから一括送信してください。")
                return
            account = settings.get("gmail_address")
        else:
            account = settings.get("account_username", "") or "未確認"
            if account == "未確認":
                QMessageBox.warning(
                    self, "送信アカウント未確認",
                    "設定タブで「サインイン／確認」を実行し、"
                    "送信アカウントを確認してから一括送信してください。")
                return
        if self.individual_match_columns:
            assigned_count = sum(
                bool(self.individual_attachments.get(index))
                for index in self.filtered_indices)
            attachment_summary = (
                f"個別添付: {assigned_count}/{len(self.filtered_indices)}件に割当済み\n"
                f"照合: {'_'.join(self.individual_match_columns)}\n\n")
        else:
            attachment_summary = "個別添付: 未設定\n\n"
        answer = QMessageBox.question(
            self, "一括送信の最終確認",
            f"{len(messages)}件を1件ずつ個別送信します。\n\n"
            f"認証アカウント: {account}\n"
            f"差出人: {from_address}\n\n"
            f"{attachment_summary}"
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
        settings, _from_address = self.selected_sender_config()
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
        self.send_status.setText(f"0/{len(messages)}件　送信を開始します")
        self.send_status.show()
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
        limit = self.current_attachment_limit()
        oversized = []
        for message in messages:
            total = sum(
                Path(path).stat().st_size
                for path in message.get("attachment_paths", [])
            )
            if total > limit:
                oversized.append(
                    f"{message.get('to_value', '')}: "
                    f"{total / (1024 * 1024):.2f} MB")
        if oversized:
            QMessageBox.warning(
                self, "再送信",
                f"安全上限の{limit / (1024 * 1024):.1f}MBを超える添付があります。\n\n"
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
        self.send_status.setText(text)
        self.send_status.show()

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
        provider = self.selected_sender_config()[0].get("provider", "m365")
        if provider == "gmail":
            result_label = "Gmailへの送信完了"
            acceptance_note = (
                "※「送信完了」はGmailのSMTPサーバーがメールを受け付けた状態です。")
        else:
            result_label = "送信要求の受理"
            acceptance_note = "※「受理」はMicrosoft 365が送信要求を受け付けた状態です。"
        result = (
            f"{status}\n"
            f"{result_label}: {success}件\n"
            f"エラー: {error}件\n\n"
            f"{acceptance_note}\n"
            "　相手に届いたことを保証するものではありません。\n"
            "　受信側の迷惑メール判定や、携帯キャリアのなりすまし規制で\n"
            "　エラーが返らないまま破棄される場合があります。"
        )
        self.send_status.setText(
            f"{status}　{result_label}: {success}件／エラー: {error}件"
            "（送信完了・受理は到達を保証しません）")
        self.send_status.show()
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
        self.current_id: int | None = None
        root = QHBoxLayout(self)

        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self.load_selected)
        new_button = QPushButton("新しいテンプレート")
        new_button.clicked.connect(self.new_template)
        delete_button = QPushButton("選択したテンプレートを削除")
        delete_button.clicked.connect(self.delete)
        left.addWidget(QLabel("登録済みテンプレート"))
        left.addWidget(self.list)
        left.addWidget(new_button)
        left.addWidget(delete_button)

        editor_box = QGroupBox("テンプレートの登録・編集")
        editor_box.setFixedWidth(420)
        editor = QVBoxLayout(editor_box)
        self.name = QLineEdit()
        self.name.setPlaceholderText("例：セミナー案内")
        self.subject = QLineEdit()
        self.subject.setPlaceholderText("件名にも {列名} を使用できます")
        self.body = QPlainTextEdit()
        self.body.setPlaceholderText("本文を入力してください。例：{参加者名} 様")
        save = QPushButton("テンプレートを保存")
        save.setObjectName("primary")
        save.clicked.connect(self.save)
        editor.addWidget(QLabel("テンプレート名"))
        editor.addWidget(self.name)
        editor.addWidget(QLabel("件名"))
        editor.addWidget(self.subject)
        editor.addWidget(QLabel("本文"))
        editor.addWidget(self.body, 1)
        editor.addWidget(save)

        left_widget = QWidget()
        left_widget.setLayout(left)
        left_widget.setMaximumWidth(330)
        root.addWidget(left_widget)
        root.addWidget(editor_box)
        root.addStretch(1)
        self.refresh()

    def refresh(self):
        selected_id = self.current_id
        self.list.clear()
        for template in self.storage.templates():
            self.list.addItem(
                f"{template['name']}（{template['updated_at'].replace('T', ' ')}）")
            self.list.item(self.list.count() - 1).setData(256, template)
        if selected_id:
            for index in range(self.list.count()):
                if self.list.item(index).data(256)["id"] == selected_id:
                    self.list.setCurrentRow(index)
                    break

    def load_selected(self, row: int):
        if row < 0:
            return
        template = self.list.item(row).data(256)
        self.current_id = template["id"]
        self.name.setText(template["name"])
        self.subject.setText(template["subject"])
        self.body.setPlainText(template["body"])

    def new_template(self):
        self.current_id = None
        self.list.clearSelection()
        self.name.clear()
        self.subject.clear()
        self.body.clear()
        self.name.setFocus()

    def save(self):
        name = self.name.text().strip()
        subject = self.subject.text()
        body = self.body.toPlainText()
        if not name or (not subject.strip() and not body.strip()):
            QMessageBox.warning(
                self, "テンプレート登録", "テンプレート名と、件名または本文を入力してください。")
            return
        existing = next(
            (item for item in self.storage.templates()
             if item["name"] == name and item["id"] != self.current_id),
            None)
        if existing and QMessageBox.question(
                self, "上書き確認", f"テンプレート「{name}」を更新しますか？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        if existing and self.current_id and existing["id"] != self.current_id:
            self.storage.delete_template(self.current_id)
            self.current_id = existing["id"]
        self.storage.save_template(name, subject, body, self.current_id)
        self.current_id = next(
            item["id"] for item in self.storage.templates()
            if item["name"] == name)
        self.refresh()
        self.changed.emit()
        QMessageBox.information(self, "テンプレート登録", f"テンプレート「{name}」を保存しました。")

    def delete(self):
        row = self.list.currentRow()
        if row < 0:
            QMessageBox.information(self, "テンプレート削除", "削除するテンプレートを選択してください。")
            return
        template = self.list.item(row).data(256)
        if QMessageBox.question(
                self, "削除確認", f"テンプレート「{template['name']}」を削除しますか？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.storage.delete_template(template["id"])
        self.new_template()
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
            ["種別", "日時", "ファイル", "件名", "総数", "受理", "エラー", "状態"])
        self.jobs.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.jobs.itemSelectionChanged.connect(self.show_logs)
        self.logs = QTableWidget(0, 6)
        self.logs.setHorizontalHeaderLabels(
            ["元データ行", "事業所名", "宛先", "件名", "結果", "エラー内容"])
        self.logs.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.logs.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        splitter = QSplitter()
        splitter.addWidget(self.jobs)
        splitter.addWidget(self.logs)
        splitter.setOrientation(__import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.Orientation.Vertical)
        buttons = QHBoxLayout()
        delete = QPushButton("選択した履歴を削除")
        delete.clicked.connect(self.delete_selected)
        retry = QPushButton("エラー・未送信を再送")
        retry.clicked.connect(self.retry_selected)
        preview = QPushButton("選択したメール内容を確認")
        preview.clicked.connect(self.preview_selected_message)
        buttons.addStretch()
        buttons.addWidget(preview)
        buttons.addWidget(retry)
        buttons.addWidget(delete)
        note = QLabel(
            "送信履歴（エラー宛先は内容を確認後、元データを修正して再送信できます）\n"
            "「受理」または「送信完了」は送信サービスがメールを受け付けた件数です。"
            "相手への到達を保証するものではありません。")
        note.setWordWrap(True)
        layout.addWidget(note)
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
                item.setToolTip(str(value))
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
                # 結果列の「成功」は受理の意味なので、表示だけ言い換える。
                if c == 4 and value == "成功":
                    value = "受理"
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                self.logs.setItem(r, c, item)
        if logs:
            self.logs.selectRow(0)

    def preview_selected_message(self):
        job_row = self.jobs.currentRow()
        log_row = self.logs.currentRow()
        if job_row < 0 or log_row < 0:
            QMessageBox.information(
                self, "メール内容の確認", "履歴とメール明細を選択してください。")
            return
        job_id = self.jobs.item(job_row, 0).data(256)
        row_number = int(self.logs.item(log_row, 0).text())
        message = self.storage.target_message(job_id, row_number)
        if not message:
            QMessageBox.information(
                self, "メール内容の確認",
                "この旧形式の履歴にはメール本文・添付情報が保存されていません。")
            return
        attachments = message.get("attachment_paths", [])
        content = (
            f"事業所名: {message.get('organization_name', '') or '（未設定）'}\n"
            f"To: {message.get('to_value', '')}\n"
            f"CC: {message.get('cc_value', '')}\n"
            f"BCC: {message.get('bcc_value', '')}\n"
            f"件名: {message.get('subject', '')}\n"
            f"添付: {', '.join(Path(path).name for path in attachments) or 'なし'}\n\n"
            f"{message.get('body', '')}"
        )
        PreviewDialog(self, "送信済みメールの内容", content).exec()

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
        editor_box.setFixedWidth(420)
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
        root.addWidget(editor_box)
        root.addStretch(1)
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


class CCContactsTab(QWidget):
    changed = pyqtSignal()

    def __init__(self, storage: Storage):
        super().__init__()
        self.storage = storage
        self.current_id: int | None = None
        root = QHBoxLayout(self)

        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self.load_selected)
        new_button = QPushButton("新しい連絡先")
        new_button.clicked.connect(self.new_contact)
        delete_button = QPushButton("選択した連絡先を削除")
        delete_button.clicked.connect(self.delete_contact)
        import_button = QPushButton("Excelからインポート")
        import_button.clicked.connect(self.import_contacts)
        left.addWidget(QLabel("よく使うCC/BCC先"))
        left.addWidget(self.list)
        left.addWidget(new_button)
        left.addWidget(delete_button)
        left.addWidget(import_button)

        editor_box = QGroupBox("連絡先の登録・編集")
        editor_box.setFixedWidth(420)
        editor = QVBoxLayout(editor_box)
        self.name = QLineEdit()
        self.name.setPlaceholderText("例：総務部 山田部長")
        self.email = QLineEdit()
        self.email.setPlaceholderText("例：yamada@example.jp")
        save = QPushButton("連絡先を保存")
        save.setObjectName("primary")
        save.clicked.connect(self.save_contact)
        editor.addWidget(QLabel("名前"))
        editor.addWidget(self.name)
        editor.addWidget(QLabel("メールアドレス"))
        editor.addWidget(self.email)
        editor.addWidget(QLabel(
            "ここに登録した連絡先は、作成・送信画面のCC・BCC欄で"
            "「連絡先から追加」から選んで使えます。"))
        editor.addWidget(save)
        editor.addStretch(1)

        left_widget = QWidget()
        left_widget.setLayout(left)
        left_widget.setMaximumWidth(330)
        root.addWidget(left_widget)
        root.addWidget(editor_box)
        root.addStretch(1)
        self.refresh()

    def refresh(self):
        selected_id = self.current_id
        self.list.clear()
        for contact in self.storage.cc_contacts():
            self.list.addItem(f"{contact['name']}（{contact['email']}）")
            self.list.item(self.list.count() - 1).setData(256, contact)
        if selected_id:
            for index in range(self.list.count()):
                if self.list.item(index).data(256)["id"] == selected_id:
                    self.list.setCurrentRow(index)
                    break

    def load_selected(self, row: int):
        if row < 0:
            return
        contact = self.list.item(row).data(256)
        self.current_id = contact["id"]
        self.name.setText(contact["name"])
        self.email.setText(contact["email"])

    def new_contact(self):
        self.current_id = None
        self.list.clearSelection()
        self.name.clear()
        self.email.clear()
        self.name.setFocus()

    def save_contact(self):
        name = self.name.text().strip()
        email = self.email.text().strip()
        if not name or not email:
            QMessageBox.warning(
                self, "連絡先登録", "名前とメールアドレスを入力してください。")
            return
        if not is_valid_email(email):
            QMessageBox.warning(self, "連絡先登録", "メールアドレスの形式が不正です。")
            return
        existing = next(
            (item for item in self.storage.cc_contacts()
             if item["name"] == name and item["id"] != self.current_id),
            None)
        if existing and QMessageBox.question(
                self, "上書き確認", f"連絡先「{name}」を更新しますか？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        if existing and self.current_id and existing["id"] != self.current_id:
            self.storage.delete_cc_contact(self.current_id)
            self.current_id = existing["id"]
        self.storage.save_cc_contact(name, email, self.current_id)
        self.current_id = next(
            item["id"] for item in self.storage.cc_contacts()
            if item["name"] == name)
        self.refresh()
        self.changed.emit()
        QMessageBox.information(self, "連絡先登録", f"連絡先「{name}」を保存しました。")

    def delete_contact(self):
        row = self.list.currentRow()
        if row < 0:
            QMessageBox.information(self, "連絡先削除", "削除する連絡先を選択してください。")
            return
        contact = self.list.item(row).data(256)
        if QMessageBox.question(
                self, "連絡先削除の確認", f"連絡先「{contact['name']}」を削除しますか？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.storage.delete_cc_contact(contact["id"])
        self.new_contact()
        self.refresh()
        self.changed.emit()

    def import_contacts(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "連絡先データを選択", "", "宛先データ (*.xlsx *.xls *.csv)")
        if not path:
            return
        try:
            result = load_recipient_file(path)
        except Exception as exc:
            QMessageBox.critical(self, "読込エラー", str(exc))
            return
        if not result.headers or not result.rows:
            QMessageBox.warning(self, "連絡先インポート", "読み込めるデータがありません。")
            return
        name_column, ok = QInputDialog.getItem(
            self, "列の選択", "名前として使う列:", result.headers, 0, False)
        if not ok:
            return
        email_column, ok = QInputDialog.getItem(
            self, "列の選択", "メールアドレスとして使う列:", result.headers, 0, False)
        if not ok:
            return
        entries = []
        invalid_count = 0
        for row in result.rows:
            name = row.get(name_column, "").strip()
            email = row.get(email_column, "").strip()
            if not name or not is_valid_email(email):
                invalid_count += 1
                continue
            entries.append((name, email))
        if not entries:
            QMessageBox.warning(self, "連絡先インポート", "登録できる行がありませんでした。")
            return
        existing_names = {contact["name"] for contact in self.storage.cc_contacts()}
        duplicate_names = sorted({name for name, _ in entries if name in existing_names})
        if duplicate_names:
            preview = "、".join(duplicate_names[:10])
            more = f"\nほか {len(duplicate_names) - 10}件" if len(duplicate_names) > 10 else ""
            answer = QMessageBox.question(
                self, "重複の確認",
                f"{len(duplicate_names)}件は既存の連絡先と同じ名前です。\n\n"
                f"{preview}{more}\n\n上書きしてよろしいですか？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        for name, email in entries:
            self.storage.save_cc_contact(name, email)
        self.refresh()
        self.changed.emit()
        message = f"{len(entries)}件を登録しました。"
        if invalid_count:
            message += f"\n（{invalid_count}件は名前またはメールアドレスが不正のためスキップしました）"
        QMessageBox.information(self, "連絡先インポート", message)


class SettingsTab(QWidget):
    changed = pyqtSignal()

    def __init__(self, storage: Storage):
        super().__init__()
        self.storage = storage
        # 設定項目は縦に長く、1366×768ではウィンドウ高さに収まらない。
        # スクロール領域へ入れないと、レイアウトが最小高さ以下へ押し潰され、
        # グループ内の行が重なって表示される。
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        layout = QVBoxLayout(content)
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
        ndr_notice = QLabel(
            "代理差出人を設定すると、配信不能通知（NDR）と返信は\n"
            "認証アカウントではなく代理差出人のアドレスに届きます。\n"
            "不達を見落とさないよう、そちらの受信トレイも確認してください。")
        ndr_notice.setWordWrap(True)
        ndr_notice.setStyleSheet("color:#713f12; background:#fef9c3; padding:6px 9px;")
        form.addRow("", ndr_notice)
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
        gmail_box = QGroupBox("Gmail（SMTP）での送信")
        gmail_form = QFormLayout(gmail_box)
        self.gmail_address = QLineEdit()
        self.gmail_app_password = QLineEdit()
        self.gmail_app_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.gmail_test_address = QLineEdit()
        gmail_form.addRow("Gmailアドレス", self.gmail_address)
        gmail_form.addRow("アプリパスワード", self.gmail_app_password)
        gmail_form.addRow("テスト送信先", self.gmail_test_address)
        gmail_test_button = QPushButton("接続テスト")
        gmail_test_button.clicked.connect(self.test_gmail_connection)
        gmail_form.addRow("", gmail_test_button)
        layout.addWidget(gmail_box)
        layout.addWidget(QLabel(
            "通常のログインパスワードではなく、Googleアカウントの2段階認証を有効にした上で\n"
            "発行する「アプリパスワード」を入力してください。"
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
        layout.addWidget(save, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(backup, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(content)
        root.addWidget(scroll)
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
        self.gmail_address.setText(settings.get("gmail_address", ""))
        self.gmail_app_password.setText(settings.get("gmail_app_password", ""))
        self.gmail_test_address.setText(settings.get("gmail_test_address", ""))

    def test_gmail_connection(self):
        config = {
            "gmail_address": self.gmail_address.text().strip(),
            "gmail_app_password": self.gmail_app_password.text(),
        }
        try:
            connection = open_gmail_connection(config)
            connection.quit()
        except Exception as exc:
            QMessageBox.critical(self, "Gmail接続テスト", str(exc))
            return
        QMessageBox.information(self, "Gmail接続テスト", "Gmailへの接続に成功しました。")

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
        for value, name in ((self.from_address.text(), "代理差出人"),
                            (self.test_address.text(), "テスト送信先"),
                            (self.gmail_address.text(), "Gmailアドレス"),
                            (self.gmail_test_address.text(), "テスト送信先（Gmail）")):
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
            "gmail_address": self.gmail_address.text().strip(),
            "gmail_app_password": self.gmail_app_password.text(),
            "gmail_test_address": self.gmail_test_address.text().strip(),
        })
        self.changed.emit()
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
        self.setMinimumSize(1250, 680)
        QApplication.instance().setStyleSheet(APP_STYLE)
        self.storage = Storage()
        retention_days = int(self.storage.settings().get("retention_days", 365))
        self.storage.delete_old_jobs(retention_days)
        file_menu = self.menuBar().addMenu("ファイル")
        user_manual_action = QAction("ユーザーマニュアルを開く", self)
        user_manual_action.triggered.connect(self.open_user_manual)
        file_menu.addAction(user_manual_action)
        self.tabs = QTabWidget()
        self.compose = ComposeTab(self.storage)
        self.templates = TemplateTab(self.storage)
        self.history = HistoryTab(self.storage)
        self.signatures = SignatureTab(self.storage)
        self.cc_contacts = CCContactsTab(self.storage)
        self.settings = SettingsTab(self.storage)
        self.tabs.addTab(self.compose, "作成・送信")
        self.tabs.addTab(self.templates, "テンプレート")
        self.tabs.addTab(self.signatures, "署名")
        self.tabs.addTab(self.cc_contacts, "連絡先")
        self.tabs.addTab(self.history, "送信履歴")
        self.tabs.addTab(self.settings, "設定")
        self.tabs.currentChanged.connect(self.refresh_current)
        self.compose.history_changed.connect(self.history.refresh)
        self.compose.sending_state_changed.connect(self.set_sending_state)
        self.templates.changed.connect(self.compose.refresh_templates)
        self.signatures.changed.connect(self.compose.refresh_signatures)
        self.settings.changed.connect(self.compose.refresh_sender_options)
        self.history.resend_requested.connect(self.resend_from_history)
        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        self.update_banner = UpdateBanner()
        central_layout.addWidget(self.update_banner)
        central_layout.addWidget(self.tabs, 1)
        self.setCentralWidget(central)
        self.statusBar().showMessage("ExcelまたはCSVを選択してください")

    def open_user_manual(self):
        """同梱したユーザーマニュアルをアプリ内で表示する。"""
        manual_path = Path(__file__).resolve().parent.parent / "docs" / "ユーザーマニュアル.md"
        try:
            content = manual_path.read_text(encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(
                self, "ユーザーマニュアル",
                f"ユーザーマニュアルを開けませんでした。\n{exc}")
            return
        PreviewDialog(self, "ユーザーマニュアル", content).exec()

    def refresh_current(self, index: int):
        if self.tabs.widget(index) is self.compose:
            self.compose.refresh_sender_options()
        elif self.tabs.widget(index) is self.templates:
            self.templates.refresh()
        elif self.tabs.widget(index) is self.signatures:
            self.signatures.refresh()
        elif self.tabs.widget(index) is self.cc_contacts:
            self.cc_contacts.refresh()
        elif self.tabs.widget(index) is self.history:
            self.history.refresh()

    def set_sending_state(self, sending: bool):
        for index in range(1, self.tabs.count()):
            self.tabs.setTabEnabled(index, not sending)
        self.update_banner.action.setEnabled(not sending)

    def resend_from_history(self, messages: list[dict]):
        self.tabs.setCurrentWidget(self.compose)
        self.compose.resend_messages(messages)

    def closeEvent(self, event):
        if self.compose.worker and self.compose.worker.isRunning():
            QMessageBox.warning(self, "送信中", "送信を中止して完了を待ってから閉じてください。")
            event.ignore()
            return
        event.accept()
