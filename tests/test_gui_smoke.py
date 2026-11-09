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
