import pytest

pytest.importorskip("PySide6.QtWidgets")


def test_window_builds(monkeypatch, tmp_path, qapp_env):
    from PySide6.QtWidgets import QApplication
    from pypixelgui.config import Settings
    from pypixelgui.device_manager import DeviceManager
    from pypixelgui.gui.main_window import MainWindow
    from fakes import FakeClient

    monkeypatch.setattr(Settings, "save", lambda self, path=None: tmp_path / "c.json")
    app = QApplication.instance() or QApplication([])
    manager = DeviceManager(client_factory=FakeClient)
    win = MainWindow(manager, Settings())
    manager.connect("AA").result(2)
    win._upsert_item("AA", "LED test")
    app.processEvents()
    win._update_controls()
    assert win.disconnect_btn.isEnabled()
    win.close()


@pytest.fixture
def qapp_env(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")


def test_theme_switch_and_scan_cleanup(monkeypatch, tmp_path, qapp_env):
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QApplication
    from pypixelgui.config import Settings
    from pypixelgui.device_manager import DeviceManager, FoundDevice
    from pypixelgui.gui.main_window import MainWindow, ADDR_ROLE
    from fakes import FakeClient

    monkeypatch.setattr(Settings, "save", lambda self, path=None: tmp_path / "c.json")
    app = QApplication.instance() or QApplication([])
    manager = DeviceManager(client_factory=FakeClient)
    win = MainWindow(manager, Settings())
    try:
        win.set_theme("dark")
        assert app.palette().color(QPalette.Window).lightness() < 80
        win.set_theme("light")
        assert app.palette().color(QPalette.Window).lightness() > 180
        win.set_theme("system")

        for a in ("GONE", "STAYS", "CONNECTED"):
            win._upsert_item(a, a)
        manager.connect("CONNECTED").result(2)
        win._on_device_event("scan_finished", {"devices": [FoundDevice("x", "STAYS")]})
        left = {win.device_list.item(i).data(ADDR_ROLE) for i in range(win.device_list.count())}
        assert left == {"STAYS", "CONNECTED"}  # connected displays don't advertise, so they stay
    finally:
        win.close()
