"""MQTT settings tab, registered with the GUI via the ``pypixelgui.tabs`` entry point."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QCheckBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from .bridge import MqttBridge, MqttSettings


class _Status(QObject):
    changed = Signal(str)


class MqttTab(QWidget):
    title = "MQTT"

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.bridge: MqttBridge | None = None
        self._status = _Status()
        self._status.changed.connect(self._show_status)

        m = MqttSettings.from_settings(window.settings)
        lay = QVBoxLayout(self)
        intro = QLabel(
            "Control saved displays from Home Assistant, Node-RED, etc. Topics: "
            "<code>&lt;base&gt;/&lt;alias&gt;/set/&lt;command&gt;</code>, e.g. "
            "<code>pypixel/desk/set/send_text</code> or <code>pypixel/desk/set/preset</code> "
            "with payload <code>CYBER</code>. Only displays saved under <i>Security</i> are reachable.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        form = QFormLayout()
        self.host = QLineEdit(m.host)
        self.port = QSpinBox(minimum=1, maximum=65535, value=m.port)
        self.user = QLineEdit(m.username)
        self.password = QLineEdit(m.password, echoMode=QLineEdit.Password)
        self.tls = QCheckBox("Use TLS", checked=m.tls)
        self.ca = QLineEdit(m.ca_certs, placeholderText="System CAs")
        self.base = QLineEdit(m.base_topic)
        self.client_id = QLineEdit(m.client_id)
        self.destructive = QCheckBox("Allow clear/delete over MQTT", checked=m.allow_destructive)
        self.autostart = QCheckBox("Start with the app", checked=m.enabled)
        for label, widget in (("Broker host", self.host), ("Port", self.port), ("Username", self.user),
                              ("Password", self.password), ("", self.tls), ("CA file", self.ca),
                              ("Base topic", self.base), ("Client ID", self.client_id),
                              ("", self.destructive), ("", self.autostart)):
            form.addRow(label, widget)
        row = QHBoxLayout()
        start = QPushButton("Save && start")
        start.clicked.connect(self.start)
        stop = QPushButton("Stop")
        stop.clicked.connect(self.stop)
        row.addWidget(start)
        row.addWidget(stop)
        row.addStretch()
        form.addRow(row)
        self.status = QLabel("Stopped")
        form.addRow("Status", self.status)
        lay.addLayout(form)
        lay.addStretch()
        if m.enabled:
            self.start()

    def _collect(self) -> MqttSettings:
        return MqttSettings(
            enabled=self.autostart.isChecked(), host=self.host.text().strip() or "localhost",
            port=self.port.value(), username=self.user.text(), password=self.password.text(),
            tls=self.tls.isChecked(), ca_certs=self.ca.text().strip(),
            client_id=self.client_id.text().strip() or "pypixelgui",
            base_topic=self.base.text().strip().strip("/") or "pypixel",
            allow_destructive=self.destructive.isChecked())

    def start(self) -> None:
        self._collect().store(self.window.settings)
        self.window.save_settings()
        self.stop()
        self.bridge = MqttBridge(self.window.manager, self.window.settings, on_status=self._status.changed.emit)
        try:
            self.bridge.start()
        except Exception as e:
            self.bridge = None
            self._show_status(f"Error: {e}")

    def stop(self) -> None:
        if self.bridge:
            self.bridge.stop()
            self.bridge = None

    shutdown = stop

    def _show_status(self, text: str) -> None:
        self.status.setText(text)
        self.window.log(f"MQTT: {text}")


def create_tab(window) -> MqttTab:
    return MqttTab(window)
