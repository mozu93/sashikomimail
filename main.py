import logging
import sys
from pathlib import Path

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMessageBox

from app.storage import data_dir
from app.ui import MainWindow


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
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
