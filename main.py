import logging
import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMessageBox

from app.storage import data_dir
from app.ui import MainWindow


def force_light_theme(app: QApplication) -> None:
    """OSがダークモードでも明るい配色で表示する。

    画面デザインは白背景を前提としているため、OS側のダークパレットが
    適用されると白背景に白文字となり、選択中の文字が読めなくなる。
    """
    try:
        app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    except AttributeError:
        # setColorSchemeはQt 6.8以降。古い環境ではスタイルシート側の
        # 文字色指定だけで対応する。
        pass


def setup_crash_logging() -> None:
    log_path = data_dir() / "sashikomi_mail.log"
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        encoding="utf-8",
    )

    def handle_exception(exc_type, exc_value, exc_traceback):
        logging.getLogger("SashikomiMail").critical(
            "未処理例外", exc_info=(exc_type, exc_value, exc_traceback))
        QMessageBox.critical(
            None, "予期しないエラー",
            f"予期しないエラーが発生しました。\n\n{exc_value}\n\n"
            f"ログ: {log_path}")

    sys.excepthook = handle_exception


def main() -> int:
    app = QApplication(sys.argv)
    setup_crash_logging()
    app.setApplicationName("差し込みメール送信")
    icon_path = Path(__file__).resolve().parent / "assets" / "icon.ico"
    app.setWindowIcon(QIcon(str(icon_path)))
    app.setStyle("Fusion")
    force_light_theme(app)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
