"""Dialog to combine saved displays into one bigger display."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QSpinBox, QVBoxLayout, QWidget,
)

from ..config import Settings, slugify
from ..groups import GroupSpec


class GroupDialog(QDialog):
    def __init__(self, parent, settings: Settings, existing: GroupSpec | None = None):
        super().__init__(parent)
        self.setWindowTitle("Combine displays")
        self.settings = settings
        self.existing = existing
        self.result_spec: GroupSpec | None = None
        lay = QVBoxLayout(self)
        intro = QLabel("Put identical displays side by side (or stacked) and use them as one. "
                       "Pick which display sits in each position, as seen from the front.")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        form = QFormLayout()
        self.name = QLineEdit(existing.name if existing else "")
        self.name.setPlaceholderText("e.g. Shop window")
        form.addRow("Name", self.name)
        size = QHBoxLayout()
        self.cols = QSpinBox(minimum=1, maximum=8, value=existing.cols if existing else 2)
        self.rows = QSpinBox(minimum=1, maximum=8, value=existing.rows if existing else 1)
        size.addWidget(self.cols)
        size.addWidget(QLabel("across ×"))
        size.addWidget(self.rows)
        size.addWidget(QLabel("down"))
        size.addStretch()
        w = QWidget()
        w.setLayout(size)
        form.addRow("Layout", w)
        lay.addLayout(form)

        self.grid_box = QGroupBox("Positions")
        self.grid = QGridLayout(self.grid_box)
        lay.addWidget(self.grid_box)
        self.cols.valueChanged.connect(self._rebuild)
        self.rows.valueChanged.connect(self._rebuild)
        self.combos: list[QComboBox] = []

        sync = QHBoxLayout()
        self.sync = QCheckBox("iPixel displays: start animations together using slot")
        self.sync.setToolTip("Saves each part to this slot, then shows it on every display at once – and\n"
                             "again every time the animation loops, so the displays don't drift apart.\n"
                             "Without it, each iPixel display runs on its own clock.\n"
                             "Pick a slot you don't use for anything else.\n"
                             "iLEDColor displays are synchronised automatically.")
        self.sync_slot = QSpinBox(minimum=1, maximum=255, value=existing.sync_slot or 9 if existing else 9)
        # New groups sync by default; scrolling text across panels needs it.
        self.sync.setChecked(bool(existing.sync_slot) if existing else True)
        sync.addWidget(self.sync)
        sync.addWidget(self.sync_slot)
        sync.addStretch()
        lay.addLayout(sync)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self._rebuild()
        if existing:
            for combo, addr in zip(self.combos, existing.members):
                combo.setCurrentIndex(max(0, combo.findData(addr)))

    def _rebuild(self) -> None:
        old = [c.currentData() for c in self.combos]
        while self.grid.count():
            self.grid.takeAt(0).widget().deleteLater()
        self.combos = []
        for r in range(self.rows.value()):
            for c in range(self.cols.value()):
                combo = QComboBox()
                combo.addItem("— choose —", None)
                for d in self.settings.devices:
                    size = ""
                    if d.panel and d.panel.get("width"):
                        size = f" ({d.panel['width']}×{d.panel['height']})"
                    combo.addItem(f"{d.name or d.address}{size}", d.address)
                i = len(self.combos)
                if i < len(old) and old[i]:
                    combo.setCurrentIndex(max(0, combo.findData(old[i])))
                elif i + 1 < combo.count():
                    combo.setCurrentIndex(i + 1)
                self.grid.addWidget(combo, r, c)
                self.combos.append(combo)

    def _accept(self) -> None:
        name = self.name.text().strip()
        if not name:
            QMessageBox.warning(self, "Combine displays", "Give the group a name.")
            return
        members = [c.currentData() for c in self.combos]
        if None in members:
            QMessageBox.warning(self, "Combine displays", "Choose a display for every position.")
            return
        alias = self.existing.alias if self.existing else slugify(name)
        taken = self.settings.taken_aliases() - ({self.existing.alias} if self.existing else set())
        base, n = alias, 2
        while alias in taken:
            alias = f"{base}_{n}"
            n += 1
        spec = GroupSpec(name, alias, self.cols.value(), self.rows.value(), members,
                         self.sync_slot.value() if self.sync.isChecked() else None,
                         hold=self.existing.hold if self.existing else False)
        try:
            spec.validate()
        except ValueError as e:
            QMessageBox.warning(self, "Combine displays", str(e))
            return
        self.result_spec = spec
        self.accept()
