"""Rotation tab: build a playlist of presets and cycle through them."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from .. import presets as presets_mod
from .. import rotation as rotation_mod


class RotationTab(QWidget):
    def __init__(self, window, icon_for):
        super().__init__()
        self.window = window
        self._icon_for = icon_for
        lay = QVBoxLayout(self)

        intro = QLabel("Pick presets and how long each stays up, then press Start. The rotation runs "
                       "on the selected display while this app is open; tick “Hold connection” so "
                       "nothing else takes over the display in between.")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        saved = QHBoxLayout()
        saved.addWidget(QLabel("Saved:"))
        self.saved_combo = QComboBox()
        self.saved_combo.setMinimumWidth(180)
        saved.addWidget(self.saved_combo)
        for text, slot in (("Load", self._load), ("Save as…", self._save_as), ("Delete", self._delete)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            saved.addWidget(b)
        saved.addStretch()
        lay.addLayout(saved)

        grid = QGridLayout()
        grid.addWidget(QLabel("Presets"), 0, 0)
        grid.addWidget(QLabel("Rotation (in order)"), 0, 2)
        self.available = QListWidget()
        self.available.setIconSize(QSize(40, 22))
        self.available.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.available.itemDoubleClicked.connect(lambda *_: self._add())
        grid.addWidget(self.available, 1, 0)
        add = QPushButton("Add ▶")
        add.clicked.connect(self._add)
        grid.addWidget(add, 1, 1, Qt.AlignVCenter)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Preset", "Seconds"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setIconSize(QSize(40, 22))
        grid.addWidget(self.table, 1, 2)
        moves = QVBoxLayout()
        for text, slot in (("▲", lambda: self._move(-1)), ("▼", lambda: self._move(1)),
                           ("Remove", self._remove), ("Clear", lambda: self.table.setRowCount(0))):
            b = QPushButton(text)
            b.clicked.connect(slot)
            moves.addWidget(b)
        moves.addStretch()
        grid.addLayout(moves, 1, 3)
        grid.setColumnStretch(0, 2)
        grid.setColumnStretch(2, 3)
        lay.addLayout(grid)

        opts = QHBoxLayout()
        self.loop = QCheckBox("Loop", checked=True)
        self.preload = QCheckBox("Load into display slots first, from slot")
        self.preload.setToolTip("Uploads every item once into the display's own memory, then only\n"
                                "switches slots: changes are instant, but those slots are overwritten.")
        self.preload_slot = QSpinBox(minimum=1, maximum=255, value=1)
        opts.addWidget(self.loop)
        opts.addSpacing(16)
        opts.addWidget(self.preload)
        opts.addWidget(self.preload_slot)
        opts.addStretch()
        lay.addLayout(opts)

        run = QHBoxLayout()
        self.start_btn = QPushButton("▶ Start")
        self.start_btn.clicked.connect(self.start)
        self.stop_btn = QPushButton("■ Stop")
        self.stop_btn.clicked.connect(self.stop)
        self.status = QLabel("Not running")
        run.addWidget(self.start_btn)
        run.addWidget(self.stop_btn)
        run.addWidget(self.status, 1)
        lay.addLayout(run)

        self.refresh_presets()
        self._refresh_saved()
        self.update_controls()

    # --- lists ------------------------------------------------------------

    def refresh_presets(self) -> None:
        self.available.clear()
        for p in presets_mod.all_presets(self.window.settings.presets):
            it = QListWidgetItem(self._icon_for(p), p.name)
            it.setToolTip(p.description)
            self.available.addItem(it)

    def _refresh_saved(self, select: str | None = None) -> None:
        self.saved_combo.clear()
        for r in self.window.settings.rotations:
            self.saved_combo.addItem(r["name"])
        if select:
            self.saved_combo.setCurrentText(select)

    def _append(self, preset_name: str, seconds: float) -> None:
        preset = presets_mod.find(preset_name, self.window.settings.presets)
        row = self.table.rowCount()
        self.table.insertRow(row)
        item = QTableWidgetItem(preset_name)
        if preset:
            item.setIcon(self._icon_for(preset))
        else:
            item.setForeground(Qt.red)
            item.setToolTip("This preset no longer exists")
        self.table.setItem(row, 0, item)
        spin = QSpinBox(minimum=1, maximum=86400, value=int(seconds), suffix=" s")
        self.table.setCellWidget(row, 1, spin)

    def _add(self) -> None:
        for it in self.available.selectedItems() or ([self.available.currentItem()]
                                                     if self.available.currentItem() else []):
            self._append(it.text(), rotation_mod.DEFAULT_SECONDS)

    def _move(self, delta: int) -> None:
        entry = self.entry()
        row = self.table.currentRow()
        new = row + delta
        if row < 0 or not 0 <= new < len(entry["items"]):
            return
        items = entry["items"]
        items[row], items[new] = items[new], items[row]
        self._fill(items)
        self.table.selectRow(new)

    def _remove(self) -> None:
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)

    def _fill(self, items: list[dict]) -> None:
        self.table.setRowCount(0)
        for it in items:
            self._append(it["preset"], it.get("seconds", rotation_mod.DEFAULT_SECONDS))

    def entry(self, name: str = "") -> dict:
        items = [{"preset": self.table.item(r, 0).text(), "seconds": self.table.cellWidget(r, 1).value()}
                 for r in range(self.table.rowCount())]
        return {"name": name, "items": items, "loop": self.loop.isChecked(),
                "preload_slot": self.preload_slot.value() if self.preload.isChecked() else None}

    # --- saved rotations -----------------------------------------------------

    def _load(self) -> None:
        entry = rotation_mod.find(self.saved_combo.currentText(), self.window.settings.rotations)
        if not entry:
            return
        self._fill(entry.get("items", []))
        self.loop.setChecked(entry.get("loop", True))
        slot = entry.get("preload_slot")
        self.preload.setChecked(slot is not None)
        if slot is not None:
            self.preload_slot.setValue(int(slot))

    def _save_as(self) -> None:
        if not self.table.rowCount():
            self.window.log("Add some presets to the rotation first.")
            return
        name, ok = QInputDialog.getText(self, "Save rotation", "Name:", text=self.saved_combo.currentText())
        name = name.strip()
        if not ok or not name:
            return
        rotations = [r for r in self.window.settings.rotations if r["name"].lower() != name.lower()]
        rotations.append(self.entry(name))
        self.window.settings.rotations = rotations
        self.window.save_settings()
        self._refresh_saved(name)
        self.window.log(f"Saved rotation “{name}”")

    def _delete(self) -> None:
        name = self.saved_combo.currentText()
        if not name or QMessageBox.question(self, "Delete rotation", f"Delete “{name}”?") != QMessageBox.Yes:
            return
        self.window.settings.rotations = [r for r in self.window.settings.rotations if r["name"] != name]
        self.window.save_settings()
        self._refresh_saved()

    # --- running ----------------------------------------------------------------

    def start(self) -> None:
        addr = self.window.current_address()
        if not addr or not self.window.manager.is_connected(addr):
            self.window.log("Connect to a display first.")
            return
        entry = self.entry("current")
        try:
            items = rotation_mod.resolve(entry, self.window.settings.presets)
        except (KeyError, ValueError) as e:
            self.window.log(f"Can't start rotation: {e}")
            return
        self.window.manager.start_rotation(addr, items, loop=entry["loop"], preload_slot=entry["preload_slot"])
        self.window.log(f"Rotation started: {len(items)} item(s)" + (", looping" if entry["loop"] else ""))
        self.window.refresh_device_list()
        self.update_controls()

    def stop(self) -> None:
        addr = self.window.current_address()
        if addr and self.window.manager.stop_rotation(addr):
            self.window.log("Rotation stopped")
        self.window.refresh_device_list()
        self.update_controls()

    def on_event(self, event: str, data: dict) -> None:
        if data.get("address") != self.window.current_address():
            return
        if event == "rotation_step":
            self.status.setText(f"Showing {data['index'] + 1}/{data['total']}: {data['name']}")
            if 0 <= data["index"] < self.table.rowCount():
                self.table.selectRow(data["index"])
        elif event == "rotation_stopped":
            self.status.setText("Stopped" + (f" – {data['message']}" if data.get("message") else ""))
        self.update_controls()

    def update_controls(self) -> None:
        addr = self.window.current_address()
        connected = bool(addr) and self.window.manager.is_connected(addr)
        running = bool(addr) and self.window.manager.rotation_running(addr)
        self.start_btn.setEnabled(connected)
        self.start_btn.setText("▶ Restart" if running else "▶ Start")
        self.stop_btn.setEnabled(running)
        if not running and self.status.text().startswith("Showing"):
            self.status.setText("Not running")
