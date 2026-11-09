"""GUI entry point."""

from __future__ import annotations

import logging
import sys


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("The GUI needs PySide6: pip install pypixelgui", file=sys.stderr)
        return 1

    from ..compat import ensure_ca_certificates
    from ..config import Settings
    from ..device_manager import DeviceManager
    from .main_window import MainWindow

    ensure_ca_certificates()
    app = QApplication(sys.argv if argv is None else argv)
    app.setApplicationName("PyPixel GUI")
    window = MainWindow(DeviceManager(), Settings.load())
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
