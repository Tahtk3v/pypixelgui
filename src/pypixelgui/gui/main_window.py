"""Main window."""

from __future__ import annotations

import datetime as _dt
import io
import logging
from importlib.metadata import entry_points
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QGridLayout, QInputDialog, QScrollArea, QCheckBox, QColorDialog, QComboBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSlider, QSpinBox, QSplitter, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from .. import media
from .. import presets as presets_mod
from ..commands import ANIMATIONS
from ..config import DeviceEntry, Settings, slugify
from ..device_manager import DeviceManager, FoundDevice
from .. import groups as groups_mod
from .group_dialog import GroupDialog
from .rotation_tab import RotationTab

log = logging.getLogger(__name__)

ADDR_ROLE = Qt.UserRole
NAME_ROLE = Qt.UserRole + 1


class _Events(QObject):
    """Moves DeviceManager callbacks (BLE thread) onto the Qt thread."""
    device = Signal(str, object)


def _preset_icon(preset) -> QIcon:
    """Thumbnail of a preset (the real flag for flags) as a Qt icon."""
    buf = io.BytesIO()
    preset.thumbnail(48, 28).save(buf, format="PNG")
    pm = QPixmap()
    pm.loadFromData(buf.getvalue(), "PNG")
    return QIcon(pm)


class ColorButton(QPushButton):
    def __init__(self, color: str = "ffffff"):
        super().__init__()
        self.setFixedWidth(90)
        self.clicked.connect(self._pick)
        self.set_color(color)

    def set_color(self, hexstr: str) -> None:
        self._color = hexstr.lower()
        c = QColor("#" + self._color)
        fg = "#000" if c.lightness() > 128 else "#fff"
        self.setText("#" + self._color)
        self.setStyleSheet(f"background:#{self._color}; color:{fg};")

    def color(self) -> str:
        return self._color

    def _pick(self) -> None:
        c = QColorDialog.getColor(QColor("#" + self._color), self, "Pick colour")
        if c.isValid():
            self.set_color(c.name()[1:])


class MainWindow(QMainWindow):
    def __init__(self, manager: DeviceManager, settings: Settings):
        super().__init__()
        self.manager = manager
        self.settings = settings
        self._plugin_tabs: list[QWidget] = []
        self._events = _Events()
        self._events.device.connect(self._on_device_event)
        manager.add_listener(lambda ev, data: self._events.device.emit(ev, data))

        self._action_buttons: list[QPushButton] = []
        self._uploading: set[str] = set()
        self._ui_ready = False
        self.setWindowTitle("PyPixel GUI")
        self.resize(1000, 700)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_device_panel())
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_presets_tab(), "Presets")
        self.rotation_tab = RotationTab(self, _preset_icon)
        self.tabs.addTab(self.rotation_tab, "Rotation")
        self.tabs.addTab(self._build_text_tab(), "Text")
        from .live_tab import LiveTab
        self.live_tab = LiveTab(self)
        live_scroll = QScrollArea(widgetResizable=True)
        live_scroll.setWidget(self.live_tab)
        self.tabs.addTab(live_scroll, "Live")
        self.tabs.addTab(self._build_image_tab(), "Image")
        self.tabs.addTab(self._build_clock_tab(), "Clock")
        self.tabs.addTab(self._build_display_tab(), "Display")
        self.tabs.addTab(self._build_extras_tab(), "Slots && extras")
        self.tabs.addTab(self._build_security_tab(), "Security")
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(1, 1)

        self.log_view = QPlainTextEdit(readOnly=True)
        self.log_view.setMaximumBlockCount(2000)
        outer = QSplitter(Qt.Vertical)
        outer.addWidget(splitter)
        outer.addWidget(self.log_view)
        outer.setSizes([520, 160])
        self.setCentralWidget(outer)
        self._build_menu()

        for dev in settings.devices:
            manager.remember(dev.address, dev.kind, dev.panel, dev.password)
            self._upsert_item(dev.address, dev.name)
            if dev.hold:
                manager.set_hold(dev.address, True)
        for g in settings.groups:
            try:
                spec = groups_mod.GroupSpec.from_dict(g)
                manager.set_group(spec)
            except (KeyError, ValueError) as e:
                log.warning("skipping invalid group %s: %s", g.get("name"), e)
                continue
            self._upsert_item(spec.address, spec.name)
            if spec.hold:
                manager.set_hold(spec.address, True)
        self._refresh_known_table()
        self._ui_ready = True
        self._update_controls()
        self._load_plugin_tabs()

    def _build_menu(self) -> None:
        from PySide6.QtGui import QAction, QActionGroup
        view = self.menuBar().addMenu("&View")
        theme_menu = view.addMenu("Theme")
        group = QActionGroup(self)
        group.setExclusive(True)
        self.theme_actions = {}
        for key, label in (("system", "Follow system"), ("light", "Light"), ("dark", "Dark")):
            act = QAction(label, self, checkable=True)
            act.setChecked(self.settings.theme == key)
            act.triggered.connect(lambda _=False, k=key: self.set_theme(k))
            group.addAction(act)
            theme_menu.addAction(act)
            self.theme_actions[key] = act

    def set_theme(self, theme: str) -> None:
        from PySide6.QtWidgets import QApplication
        from .theme import apply
        self.settings.theme = theme
        apply(QApplication.instance(), theme)
        self.save_settings()

    def _load_plugin_tabs(self) -> None:
        """Add tabs from installed add-ons (entry point group ``pypixelgui.tabs``)."""
        for ep in entry_points(group="pypixelgui.tabs"):
            try:
                tab = ep.load()(self)
            except Exception as e:
                log.exception("add-on tab %s failed", ep.name)
                self.log(f"Add-on “{ep.name}” failed to load: {e}")
                continue
            self._plugin_tabs.append(tab)
            self.tabs.addTab(tab, getattr(tab, "title", ep.name))

    # ------------------------------------------------------------------ UI

    def _build_device_panel(self) -> QWidget:
        box = QGroupBox("Displays")
        lay = QVBoxLayout(box)
        self.device_list = QListWidget()
        self.device_list.currentItemChanged.connect(lambda *_: self._update_controls())
        self.device_list.itemDoubleClicked.connect(lambda *_: self._connect_selected())
        lay.addWidget(self.device_list)

        row = QHBoxLayout()
        self.scan_btn = QPushButton("Scan")
        self.scan_btn.clicked.connect(self._scan)
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self._connect_selected)
        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.clicked.connect(self._disconnect_selected)
        for b in (self.scan_btn, self.connect_btn, self.disconnect_btn):
            row.addWidget(b)
        lay.addLayout(row)

        self.hold_check = QCheckBox("Hold connection (lock out other apps)")
        self.hold_check.setToolTip(
            "Keep this display connected and reconnect automatically if it drops.\n"
            "These displays accept one Bluetooth connection at a time, so while\n"
            "this app holds it, phones and other apps can't take over.")
        self.hold_check.clicked.connect(self._toggle_hold)
        lay.addWidget(self.hold_check)

        self.info_label = QLabel("No display selected")
        self.info_label.setWordWrap(True)
        self.info_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.info_label)
        box.setMinimumWidth(280)
        return box

    def _send_row(self, text: str, slot, command: str | None = None) -> QPushButton:
        """A button that needs a connected display (and, if given, support for ``command``)."""
        btn = QPushButton(text)
        btn.clicked.connect(slot)
        btn.setProperty("command", command)
        self._action_buttons.append(btn)
        return btn

    def _build_presets_tab(self) -> QWidget:
        outer = QWidget()
        lay = QVBoxLayout(outer)
        hint = QLabel("One click sends a ready-made look, sized for the connected display.")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        scroll = QScrollArea(widgetResizable=True)
        self.presets_box = QWidget()
        self.presets_layout = QVBoxLayout(self.presets_box)
        scroll.setWidget(self.presets_box)
        lay.addWidget(scroll)
        row = QHBoxLayout()
        remove = QPushButton("Remove one of my presets…")
        remove.clicked.connect(self._remove_user_preset)
        row.addStretch()
        row.addWidget(remove)
        lay.addLayout(row)
        self._populate_presets()
        return outer

    def _populate_presets(self) -> None:
        while self.presets_layout.count():
            item = self.presets_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for b in getattr(self, "_preset_buttons", []):
            if b in self._action_buttons:
                self._action_buttons.remove(b)
        self._preset_buttons: list[QPushButton] = []
        by_cat: dict[str, list[presets_mod.Preset]] = {}
        by_cat["My presets"] = []  # your own presets come first
        for p in presets_mod.all_presets(self.settings.presets):
            by_cat.setdefault(p.category, []).append(p)
        if not by_cat["My presets"]:
            del by_cat["My presets"]
        for cat, items in by_cat.items():
            box = QGroupBox(cat)
            grid = QGridLayout(box)
            for i, p in enumerate(items):
                btn = QPushButton(p.name)
                btn.setToolTip(p.description)
                btn.setMinimumHeight(44)
                btn.setIcon(_preset_icon(p))
                btn.setIconSize(QSize(48, 28))
                btn.setStyleSheet("text-align: left; padding: 4px 10px;")
                btn.clicked.connect(lambda _=False, preset=p: self._run_preset(preset))
                grid.addWidget(btn, i // 3, i % 3)
                self._preset_buttons.append(btn)
                self._action_buttons.append(btn)
            self.presets_layout.addWidget(box)
        self.presets_layout.addStretch()
        if hasattr(self, "rotation_tab"):
            self.rotation_tab.refresh_presets()
        if self._ui_ready:
            self._update_controls()

    def _run_preset(self, preset: presets_mod.Preset) -> None:
        addr = self.current_address()
        if not addr or not self.manager.is_connected(addr):
            self.log("Connect to a display first.")
            return
        self._interrupt_rotation(addr)
        self.manager.run_preset(addr, preset)

    def _interrupt_rotation(self, addr: str) -> None:
        """Anything sent by hand takes over the display, so the rotation stops."""
        if self.manager.stop_rotation(addr):
            self.log("Rotation stopped because you sent something by hand.")

    def _save_text_as_preset(self) -> None:
        params = self._text_params()
        if params is None:
            return
        params.pop("font", None)  # presets must work remotely, so no local font files
        name, ok = QInputDialog.getText(self, "Save preset", "Preset name:", text=params["text"][:20])
        name = name.strip()
        if not ok or not name:
            return
        if any(p.name.lower() == name.lower() for p in presets_mod.BUILTIN):
            QMessageBox.warning(self, "Name taken", f"“{name}” is a built-in preset name.")
            return
        self.settings.presets = [e for e in self.settings.presets if e["name"].lower() != name.lower()]
        swatch = [params["color"]] + ([params["bg_color"]] if "bg_color" in params else [])
        self.settings.presets.append({"name": name, "description": f"“{params['text']}”",
                                      "swatch": swatch, "steps": [["send_text", params]]})
        self.save_settings()
        self._populate_presets()
        self.log(f"Saved preset “{name}” – it's at the top of the Presets tab")

    def _save_image_as_preset(self) -> None:
        path = Path(self.image_path.text().strip())
        if not path.is_file():
            self.log("Pick an image file first.")
            return
        name, ok = QInputDialog.getText(self, "Save preset", "Preset name:", text=path.stem[:20])
        name = name.strip()
        if not ok or not name:
            return
        if any(p.name.lower() == name.lower() for p in presets_mod.BUILTIN):
            QMessageBox.warning(self, "Name taken", f"“{name}” is a built-in preset name.")
            return
        import hashlib
        import shutil
        from ..config import media_dir
        data = path.read_bytes()
        stored = hashlib.sha1(data).hexdigest()[:16] + path.suffix.lower()
        try:
            media_dir().mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, media_dir() / stored)
        except OSError as e:
            self.log(f"Could not store the image: {e}")
            return
        self.settings.presets = [e for e in self.settings.presets if e["name"].lower() != name.lower()]
        self.settings.presets.append({
            "name": name, "description": f"Image: {path.name}",
            "steps": [["image_file", {"file": stored, "resize_method": self.resize_combo.currentText(),
                                      "budget_kb": self.image_budget.value()}]]})
        self.save_settings()
        self._populate_presets()
        self.log(f"Saved preset “{name}” – it's at the top of the Presets tab")

    def _remove_user_preset(self) -> None:
        names = [e["name"] for e in self.settings.presets]
        if not names:
            self.log("You haven't saved any presets yet (Text tab → Save as preset).")
            return
        name, ok = QInputDialog.getItem(self, "Remove preset", "Preset:", names, 0, False)
        if ok:
            self.settings.presets = [e for e in self.settings.presets if e["name"] != name]
            self.save_settings()
            self._populate_presets()

    def _build_text_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText("Text to show (emoji supported)")
        self.text_edit.setMaximumHeight(90)
        form.addRow("Text", self.text_edit)
        self.text_color = ColorButton("ffffff")
        form.addRow("Colour", self.text_color)
        bg_row = QHBoxLayout()
        self.bg_enable = QCheckBox("Background")
        self.bg_color = ColorButton("000000")
        bg_row.addWidget(self.bg_enable)
        bg_row.addWidget(self.bg_color)
        bg_row.addStretch()
        form.addRow("", self._wrap(bg_row))
        self.anim_combo = QComboBox()
        self.anim_combo.addItems([a.replace("_", " ").title() for a in ANIMATIONS])
        self.anim_combo.setCurrentIndex(1)
        form.addRow("Animation", self.anim_combo)
        self.speed_slider = QSlider(Qt.Horizontal, minimum=0, maximum=100, value=80)
        form.addRow("Speed", self.speed_slider)
        self.rainbow_spin = QSpinBox(minimum=0, maximum=9)
        self.rainbow_spin.setSpecialValueText("Off")
        form.addRow("Rainbow mode", self.rainbow_spin)
        self.text_slot = self._slot_spin()
        form.addRow("Save to slot", self.text_slot)
        font_row = QHBoxLayout()
        self.font_path = QLineEdit(placeholderText="Built-in (Unifont)")
        browse = QPushButton("…")
        browse.clicked.connect(self._pick_font)
        font_row.addWidget(self.font_path)
        font_row.addWidget(browse)
        form.addRow("Font file", self._wrap(font_row))
        trow = QHBoxLayout()
        trow.addWidget(self._send_row("Send text", self._send_text))
        save_preset = QPushButton("Save as preset…")
        save_preset.clicked.connect(self._save_text_as_preset)
        trow.addWidget(save_preset)
        form.addRow(self._wrap(trow))
        return w

    def _build_image_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        row = QHBoxLayout()
        self.image_path = QLineEdit(placeholderText="PNG, JPG, GIF…")
        self.image_path.textChanged.connect(self._update_preview)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._pick_image)
        row.addWidget(self.image_path)
        row.addWidget(browse)
        form.addRow("File", self._wrap(row))
        self.resize_combo = QComboBox()
        self.resize_combo.addItems(["crop", "fit"])
        form.addRow("Resize", self.resize_combo)
        self.image_slot = self._slot_spin()
        form.addRow("Save to slot", self.image_slot)
        self.image_budget = QSpinBox(minimum=10, maximum=4000, value=int(media.DEFAULT_BUDGET_KB),
                                     singleStep=10, suffix=" KB")
        self.image_budget.setToolTip(
            "Animations bigger than this get every 2nd/3rd/… frame dropped (keeping the same speed).\n"
            "Large uploads take minutes over Bluetooth and the display may disconnect or run out of\n"
            "memory part-way. Raise it if your display copes with more.")
        form.addRow("Max size", self.image_budget)
        self.preview = QLabel("No image")
        self.preview.setMinimumHeight(140)
        self.preview.setAlignment(Qt.AlignCenter)
        form.addRow("Preview", self.preview)
        self.send_image_btn = self._send_row("Send image", self._send_image)
        save_img = QPushButton("Save as preset…")
        save_img.clicked.connect(self._save_image_as_preset)
        irow = QHBoxLayout()
        irow.addWidget(self.send_image_btn)
        irow.addWidget(save_img)
        form.addRow(self._wrap(irow))
        self.upload_bar = QProgressBar()
        self.upload_bar.setVisible(False)
        form.addRow("", self.upload_bar)
        return w

    def _build_clock_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.clock_style = QSpinBox(minimum=0, maximum=8, value=1)
        form.addRow("Style", self.clock_style)
        self.clock_date = QCheckBox("Show date", checked=True)
        self.clock_24 = QCheckBox("24-hour", checked=True)
        form.addRow("", self.clock_date)
        form.addRow("", self.clock_24)
        form.addRow(self._send_row("Show clock", lambda: self._run("set_clock_mode", {
            "style": self.clock_style.value(), "show_date": self.clock_date.isChecked(),
            "format_24": self.clock_24.isChecked()}), "set_clock_mode"))
        form.addRow(self._send_row("Sync time from this computer", self._sync_time, "set_time"))
        return w

    def _build_display_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.brightness = QSlider(Qt.Horizontal, minimum=0, maximum=100, value=50)
        self.brightness_label = QLabel("50%")
        self.brightness.valueChanged.connect(lambda v: self.brightness_label.setText(f"{v}%"))
        self.brightness.sliderReleased.connect(
            lambda: self._run("set_brightness", {"level": self.brightness.value()}))
        row = QHBoxLayout()
        row.addWidget(self.brightness)
        row.addWidget(self.brightness_label)
        form.addRow("Brightness", self._wrap(row))
        prow = QHBoxLayout()
        prow.addWidget(self._send_row("On", lambda: self._run("set_power", {"on": True}), "set_power"))
        prow.addWidget(self._send_row("Off", lambda: self._run("set_power", {"on": False}), "set_power"))
        form.addRow("Power", self._wrap(prow))
        self.orientation = QComboBox()
        self.orientation.addItems(["0°", "90°", "180°", "270°"])
        orow = QHBoxLayout()
        orow.addWidget(self.orientation)
        orow.addWidget(self._send_row("Apply", lambda: self._run(
            "set_orientation", {"orientation": self.orientation.currentIndex()}), "set_orientation"))
        form.addRow("Orientation", self._wrap(orow))
        self.fun_mode = QCheckBox("Fun mode (enables per-pixel drawing)")
        self.fun_mode.clicked.connect(lambda on: self._run("set_fun_mode", {"enable": on}))
        form.addRow("", self.fun_mode)
        return w

    def _build_extras_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.show_slot_spin = self._slot_spin(1)
        srow = QHBoxLayout()
        srow.addWidget(self.show_slot_spin)
        srow.addWidget(self._send_row("Show", lambda: self._run(
            "show_slot", {"number": self.show_slot_spin.value()}), "show_slot"))
        srow.addWidget(self._send_row("Delete…", self._delete_slot, "delete"))
        form.addRow("Slot", self._wrap(srow))
        self.p1 = QSpinBox(maximum=999)
        self.p2 = QSpinBox(maximum=999)
        sc = QHBoxLayout()
        sc.addWidget(self.p1)
        sc.addWidget(QLabel(":"))
        sc.addWidget(self.p2)
        sc.addWidget(self._send_row("Set", lambda: self._run(
            "set_scores", {"score_p1": self.p1.value(), "score_p2": self.p2.value()}), "set_scores"))
        form.addRow("Scoreboard", self._wrap(sc))
        trow = QHBoxLayout()
        for action in ("start", "pause", "stop"):
            trow.addWidget(self._send_row(action.title(),
                                          lambda _=False, a=action: self._run("set_timer", {"action": a}),
                                          "set_timer"))
        form.addRow("Timer", self._wrap(trow))
        form.addRow(self._send_row("Clear all stored content…", self._clear_all, "clear"))
        return w

    def _build_security_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        sec = QGroupBox("Access control")
        sl = QVBoxLayout(sec)
        sl.addWidget(QLabel(
            "<b>Hold</b> keeps a display connected so other phones/apps can't connect to it. "
            "Displays ticked under <i>Hold</i> below are locked automatically when the app "
            "(or <code>pypixelgui-bridge</code>) starts. Only displays listed here can be "
            "controlled remotely (e.g. over MQTT, if the add-on is installed)."))
        sl.itemAt(0).widget().setWordWrap(True)
        self.known_only = QCheckBox("Only allow connecting to saved displays",
                                    checked=self.settings.known_devices_only)
        self.known_only.toggled.connect(self._set_known_only)
        sl.addWidget(self.known_only)
        self.known_table = QTableWidget(0, 4)
        self.known_table.setHorizontalHeaderLabels(["Name", "Address", "Remote alias", "Hold"])
        self.known_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.known_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.known_table.itemChanged.connect(self._known_table_edited)
        sl.addWidget(self.known_table)
        krow = QHBoxLayout()
        save_dev = QPushButton("Save selected display")
        save_dev.clicked.connect(self._save_selected_device)
        forget = QPushButton("Forget")
        forget.clicked.connect(self._forget_device)
        krow.addWidget(save_dev)
        krow.addWidget(forget)
        krow.addStretch()
        combine = QPushButton("Combine displays…")
        combine.setToolTip("Use several identical displays as one bigger display")
        combine.clicked.connect(lambda: self._edit_group(None))
        edit_group = QPushButton("Edit group…")
        edit_group.clicked.connect(lambda: self._edit_group(self.manager.group(self.current_address() or "")))
        del_group = QPushButton("Delete group")
        del_group.clicked.connect(self._delete_group)
        krow.addWidget(combine)
        krow.addWidget(edit_group)
        krow.addWidget(del_group)
        sl.addLayout(krow)
        lay.addWidget(sec)

        pw = QGroupBox("Display password (iLEDColor displays)")
        pl = QVBoxLayout(pw)
        note = QLabel(
            "iLEDColor displays can store a 6-character password. Testers found it guards the "
            "display's settings, but on their units it did <b>not</b> stop new pictures being sent – "
            "so treat it as a lock against casual tampering. A forgotten password can't be recovered. "
            "<b>Hold</b> above is what actually keeps others out while this app runs.")
        note.setWordWrap(True)
        pl.addWidget(note)
        prow = QHBoxLayout()
        self.pw_current = QLineEdit(echoMode=QLineEdit.Password, maxLength=6,
                                    placeholderText="Current password (if any)")
        self.pw_new = QLineEdit(echoMode=QLineEdit.Password, maxLength=6, placeholderText="New password")
        prow.addWidget(self.pw_current)
        prow.addWidget(self.pw_new)
        pl.addLayout(prow)
        brow = QHBoxLayout()
        self.pw_set_btn = QPushButton("Set / change password")
        self.pw_set_btn.clicked.connect(self._set_device_password)
        self.pw_clear_btn = QPushButton("Remove password…")
        self.pw_clear_btn.clicked.connect(self._clear_device_password)
        brow.addWidget(self.pw_set_btn)
        brow.addWidget(self.pw_clear_btn)
        brow.addStretch()
        pl.addLayout(brow)
        lay.addWidget(pw)
        self.pw_box = pw
        return w

    def _update_password_controls(self, addr: str | None, connected: bool) -> None:
        if not hasattr(self, "pw_box"):
            return
        ok = connected and self.manager.kind(addr) == "iledcolor"
        self.pw_box.setEnabled(ok)

    def _password_target(self) -> str | None:
        addr = self.current_address()
        if not addr or not self.manager.is_connected(addr) or self.manager.kind(addr) != "iledcolor":
            self.log("Connect to an iLEDColor display first.")
            return None
        return addr

    def _set_device_password(self) -> None:
        addr = self._password_target()
        new, old = self.pw_new.text(), self.pw_current.text()
        if not addr:
            return
        if len(new) != 6 or not new.isascii():
            QMessageBox.warning(self, "Password", "The password must be exactly 6 letters or digits.")
            return
        info = self.manager.device_info(addr)
        has_pw = info is not None and info.password_flag != 255
        mode = 1 if has_pw else 0
        fut = self.manager.device_password(addr, mode, old if has_pw else "", new)
        fut.add_done_callback(lambda f, a=addr, n=new: self._events.device.emit(
            "_password_result", {"address": a, "new": n, "error": None if f.cancelled() or f.exception() is None
                                 else str(f.exception())}))

    def _clear_device_password(self) -> None:
        addr = self._password_target()
        if not addr:
            return
        old = self.pw_current.text()
        if QMessageBox.question(self, "Remove password", "Remove the password from this display?") \
                != QMessageBox.Yes:
            return
        fut = self.manager.device_password(addr, 2, old, "")
        fut.add_done_callback(lambda f, a=addr: self._events.device.emit(
            "_password_result", {"address": a, "new": "", "error": None if f.cancelled() or f.exception() is None
                                 else str(f.exception())}))

    @staticmethod
    def _wrap(layout) -> QWidget:
        layout.setContentsMargins(0, 0, 0, 0)
        w = QWidget()
        w.setLayout(layout)
        return w

    @staticmethod
    def _slot_spin(minimum: int = 0) -> QSpinBox:
        s = QSpinBox(minimum=minimum, maximum=255)
        if minimum == 0:
            s.setSpecialValueText("Don't save")
        return s

    # ------------------------------------------------------------ helpers

    def log(self, msg: str) -> None:
        ts = _dt.datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{ts}] {msg}")

    def current_address(self) -> str | None:
        item = self.device_list.currentItem()
        return item.data(ADDR_ROLE) if item else None

    def _label_for(self, address: str) -> str:
        spec = self.manager.group(address)
        if spec is not None:
            return spec.name
        dev = self.settings.find(address)
        return (dev.name if dev and dev.name else None) or address

    def _upsert_item(self, address: str, name: str = "") -> QListWidgetItem:
        for i in range(self.device_list.count()):
            it = self.device_list.item(i)
            if it.data(ADDR_ROLE) == address:
                if name:
                    it.setData(NAME_ROLE, name)
                self._render_item(it)
                return it
        it = QListWidgetItem()
        it.setData(ADDR_ROLE, address)
        it.setData(NAME_ROLE, name)
        self.device_list.addItem(it)
        self._render_item(it)
        if self.device_list.currentItem() is None:
            self.device_list.setCurrentItem(it)
        return it

    def _drop_missing(self, found: set[str]) -> int:
        """After a scan, remove displays it didn't see – except connected ones (they don't
        advertise while connected) and groups (virtual). Saved displays stay saved."""
        current = self.current_address()
        removed = 0
        for i in reversed(range(self.device_list.count())):
            addr = self.device_list.item(i).data(ADDR_ROLE)
            if addr in found or groups_mod.is_group(addr) or self.manager.is_connected(addr) \
                    or self.manager.is_held(addr):
                continue
            self.device_list.takeItem(i)
            removed += 1
        if current and self.current_address() != current:
            self._update_controls()
        return removed

    def _render_item(self, it: QListWidgetItem) -> None:
        addr = it.data(ADDR_ROLE)
        tags = []
        if self.manager.is_connected(addr):
            tags.append("● connected")
        if self.manager.is_held(addr):
            tags.append("🔒 held")
        if self.manager.rotation_running(addr):
            tags.append("⟳ rotating")
        if self.settings.find(addr):
            tags.append("saved")
        spec = self.manager.group(addr)
        if spec is not None:
            info = self.manager.device_info(addr) if self.manager.is_connected(addr) else None
            size = f" · {info.width}×{info.height}" if info else ""
            tags.insert(0, f"Group of {len(spec.members)} ({spec.cols}×{spec.rows}){size}")
            if "saved" in tags:
                tags.remove("saved")
        elif self.manager.kind(addr) == "iledcolor":
            panel = self.manager._hints.get(addr, {}).get("panel") or {}
            size = f" {panel['width']}×{panel['height']}" if panel.get("width") else ""
            tags.insert(0, f"iLEDColor{size}")
        name = it.data(NAME_ROLE) or "LED display"
        it.setText(f"{name}\n{addr}" + (f"\n{'  ·  '.join(tags)}" if tags else ""))

    def refresh_device_list(self) -> None:
        for i in range(self.device_list.count()):
            self._render_item(self.device_list.item(i))

    def _update_controls(self) -> None:
        addr = self.current_address()
        connected = bool(addr) and self.manager.is_connected(addr)
        self.connect_btn.setEnabled(bool(addr) and not connected)
        self.disconnect_btn.setEnabled(connected)
        self.hold_check.setEnabled(bool(addr))
        self.hold_check.setChecked(bool(addr) and self.manager.is_held(addr))
        caps = self.manager.capabilities(addr) if connected else None
        supported = lambda cmd: cmd is None or caps is None or cmd in caps  # noqa: E731
        for b in self._action_buttons:
            cmd = b.property("command")
            b.setEnabled(connected and supported(cmd))
            b.setToolTip("" if supported(cmd) else "This display doesn't support this")
        uploading = bool(addr) and addr in self._uploading
        self.send_image_btn.setEnabled(connected and not uploading)
        self.send_image_btn.setText("Uploading…" if uploading else "Send image")
        self.fun_mode.setEnabled(connected and supported("set_fun_mode"))
        self._update_password_controls(addr, connected)
        if hasattr(self, "rotation_tab"):
            self.rotation_tab.update_controls()
        if not addr:
            self.info_label.setText("No display selected")
        elif connected:
            info = self.manager.device_info(addr)
            if info is None:
                self.info_label.setText("<b>Connected</b>")
            else:
                kind = getattr(info, "kind", "")
                family = {"iledcolor": "iLEDColor",
                          "group": f"group of {getattr(info, 'panels', '?')} displays"}.get(
                              kind, f"iPixel type {info.led_type}")
                pw = "none" if info.password_flag == 255 else "set"
                self.info_label.setText(f"<b>Connected</b><br>{info.width}×{info.height} · {family}<br>"
                                        f"Device password: {pw}")
        else:
            self.info_label.setText("Not connected")

    # ------------------------------------------------------------- actions

    def _scan(self) -> None:
        self.scan_btn.setEnabled(False)
        self.scan_btn.setText("Scanning…")
        self.manager.scan(5.0)

    def _connect_selected(self) -> None:
        addr = self.current_address()
        if not addr:
            return
        if self.settings.known_devices_only and not self.settings.find(addr) and not groups_mod.is_group(addr):
            QMessageBox.warning(self, "Not allowed",
                                "Only saved displays may be connected. Save it under Security, "
                                "or turn off “Only allow connecting to saved displays”.")
            return
        self.manager.connect(addr)

    def _disconnect_selected(self) -> None:
        addr = self.current_address()
        if addr:
            self.manager.disconnect(addr)

    def _toggle_hold(self, on: bool) -> None:
        addr = self.current_address()
        if not addr:
            return
        if on and self.settings.known_devices_only and not self.settings.find(addr):
            self.hold_check.setChecked(False)
            return
        self.manager.set_hold(addr, on)
        g = self.settings.group_by_alias(addr[len(groups_mod.PREFIX):]) if groups_mod.is_group(addr) else None
        if g is not None:
            g["hold"] = on
            self.save_settings()
        dev = self.settings.find(addr)
        if dev:
            dev.hold = on
            self.save_settings()
            self._refresh_known_table()

    def _run(self, command: str, params: dict | None = None) -> None:
        addr = self.current_address()
        if not addr or not self.manager.is_connected(addr):
            self.log("Connect to a display first.")
            return
        self._interrupt_rotation(addr)
        self.manager.run(addr, command, params or {})

    def _send_text(self) -> None:
        params = self._text_params()
        if params is not None:
            self._run("send_text", params)

    def _text_params(self) -> dict | None:
        text = self.text_edit.toPlainText().strip()
        if not text:
            self.log("Enter some text first.")
            return None
        params = {
            "text": text, "color": self.text_color.color(),
            "animation": ANIMATIONS[self.anim_combo.currentIndex()],
            "speed": self.speed_slider.value(), "rainbow_mode": self.rainbow_spin.value(),
            "save_slot": self.text_slot.value(),
        }
        if self.bg_enable.isChecked():
            params["bg_color"] = self.bg_color.color()
        if self.font_path.text().strip():
            params["font"] = self.font_path.text().strip()
        return params

    def _send_image(self) -> None:
        path = self.image_path.text().strip()
        if not path or not Path(path).is_file():
            self.log("Pick an image file first.")
            return
        addr = self.current_address()
        if not addr or not self.manager.is_connected(addr):
            self.log("Connect to a display first.")
            return
        if addr in self._uploading:
            self.log("An image is still uploading to this display – wait for it to finish.")
            return
        self._interrupt_rotation(addr)
        self._uploading.add(addr)
        self._update_controls()
        fut = self.manager.send_image_file(addr, path, self.resize_combo.currentText(),
                                           self.image_slot.value(), self.image_budget.value())
        fut.add_done_callback(lambda _f, a=addr: self._events.device.emit("_upload_done", {"address": a}))

    def _sync_time(self) -> None:
        now = _dt.datetime.now()
        self._run("set_time", {"hour": now.hour, "minute": now.minute, "second": now.second})

    def _delete_slot(self) -> None:
        n = self.show_slot_spin.value()
        if QMessageBox.question(self, "Delete slot", f"Delete slot {n} from the display?") == QMessageBox.Yes:
            self._run("delete", {"n": n})

    def _clear_all(self) -> None:
        if QMessageBox.question(self, "Clear display",
                                "Erase all stored content on the display? This can't be undone.") \
                == QMessageBox.Yes:
            self._run("clear")

    def _pick_font(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Font", "", "Fonts (*.ttf *.otf *.bdf);;All files (*)")
        if path:
            self.font_path.setText(path)

    def _pick_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Image", "",
                                              "Images (*.png *.jpg *.jpeg *.gif *.bmp *.webp);;All files (*)")
        if path:
            self.image_path.setText(path)

    def _update_preview(self, path: str) -> None:
        pm = QPixmap(path) if path and Path(path).is_file() else QPixmap()
        if pm.isNull():
            self.preview.setText("No image")
            self.preview.setPixmap(QPixmap())
            return
        info = self.manager.device_info(self.current_address() or "")
        if info:  # show roughly what the matrix will get
            mode = Qt.KeepAspectRatioByExpanding if self.resize_combo.currentText() == "crop" \
                else Qt.KeepAspectRatio
            pm = pm.scaled(info.width, info.height, mode, Qt.FastTransformation)
        self.preview.setPixmap(pm.scaled(self.preview.width(), 140, Qt.KeepAspectRatio, Qt.FastTransformation))

    # ------------------------------------------------- known devices / security

    def save_settings(self) -> None:
        try:
            self.settings.save()
        except OSError as e:
            self.log(f"Could not save settings: {e}")
        for tab in getattr(self, "_plugin_tabs", []):
            if hasattr(tab, "settings_changed"):
                try:
                    tab.settings_changed()
                except Exception:
                    log.exception("add-on failed to handle a settings change")

    def _set_known_only(self, on: bool) -> None:
        self.settings.known_devices_only = on
        self.save_settings()

    def _save_selected_device(self) -> None:
        item = self.device_list.currentItem()
        if not item:
            return
        addr, name = item.data(ADDR_ROLE), item.data(NAME_ROLE) or ""
        hint = self.manager._hints.get(addr, {})
        dev = self.settings.upsert(DeviceEntry(address=addr, name=name, hold=self.manager.is_held(addr),
                                               kind=hint.get("kind", "ipixel"), panel=hint.get("panel"),
                                               password=hint.get("password", "")))
        dev.kind, dev.panel = hint.get("kind", dev.kind), hint.get("panel") or dev.panel
        self.save_settings()
        self._refresh_known_table()
        self.refresh_device_list()
        self.log(f"Saved {name or addr} (remote alias “{dev.alias}”)")

    def _edit_group(self, existing) -> None:
        if existing is None and self.sender() and self.sender().text().startswith("Edit"):
            self.log("Select a group in the display list first.")
            return
        if len(self.settings.devices) < 2:
            QMessageBox.information(self, "Combine displays",
                                    "Save at least two displays first (connect each, then “Save selected display”).")
            return
        dlg = GroupDialog(self, self.settings, existing)
        if dlg.exec() != QDialog.Accepted or dlg.result_spec is None:
            return
        spec = dlg.result_spec
        if existing is not None:
            self.manager.remove_group(existing.alias).result(10)
        self.manager.set_group(spec)
        self.settings.groups = [g for g in self.settings.groups if g.get("alias") != spec.alias] + [spec.to_dict()]
        self.save_settings()
        it = self._upsert_item(spec.address, spec.name)
        self.device_list.setCurrentItem(it)
        self.log(f"Group “{spec.name}” ready ({spec.cols}×{spec.rows}). Connect it like any display.")

    def _delete_group(self) -> None:
        spec = self.manager.group(self.current_address() or "")
        if spec is None:
            self.log("Select a group in the display list first.")
            return
        if QMessageBox.question(self, "Delete group", f"Delete the group “{spec.name}”? "
                                "The displays themselves are kept.") != QMessageBox.Yes:
            return
        self.manager.remove_group(spec.alias).result(10)
        self.settings.groups = [g for g in self.settings.groups if g.get("alias") != spec.alias]
        self.save_settings()
        for i in range(self.device_list.count()):
            if self.device_list.item(i).data(ADDR_ROLE) == spec.address:
                self.device_list.takeItem(i)
                break
        self._update_controls()

    def _forget_device(self) -> None:
        row = self.known_table.currentRow()
        if row < 0:
            return
        dev = self.settings.devices.pop(row)
        if self.manager.is_held(dev.address):
            self.manager.set_hold(dev.address, False)
        self.save_settings()
        self._refresh_known_table()
        self.refresh_device_list()

    def _refresh_known_table(self) -> None:
        self.known_table.blockSignals(True)
        self.known_table.setRowCount(len(self.settings.devices))
        for r, dev in enumerate(self.settings.devices):
            name = QTableWidgetItem(dev.name)
            addr = QTableWidgetItem(dev.address)
            addr.setFlags(addr.flags() & ~Qt.ItemIsEditable)
            alias = QTableWidgetItem(dev.alias)
            hold = QTableWidgetItem()
            hold.setFlags((hold.flags() | Qt.ItemIsUserCheckable) & ~Qt.ItemIsEditable)
            hold.setCheckState(Qt.Checked if dev.hold else Qt.Unchecked)
            for c, it in enumerate((name, addr, alias, hold)):
                self.known_table.setItem(r, c, it)
        self.known_table.blockSignals(False)

    def _known_table_edited(self, item: QTableWidgetItem) -> None:
        dev = self.settings.devices[item.row()]
        col = item.column()
        if col == 0:
            dev.name = item.text().strip()
            self._upsert_item(dev.address, dev.name)
        elif col == 2:
            alias = slugify(item.text())
            if any(d.alias == alias and d is not dev for d in self.settings.devices):
                self.log(f"Alias “{alias}” is already used.")
                alias = dev.alias
            dev.alias = alias
            self._refresh_known_table()
        elif col == 3:
            dev.hold = item.checkState() == Qt.Checked
            self.manager.set_hold(dev.address, dev.hold)
        self.save_settings()

    # -------------------------------------------------------------- events

    def _on_device_event(self, event: str, data: dict) -> None:
        addr = data.get("address", "")
        label = self._label_for(addr) if addr else ""
        if event == "scan_finished":
            self.scan_btn.setEnabled(True)
            self.scan_btn.setText("Scan")
            devices: list[FoundDevice] = data["devices"]
            for d in devices:
                self._upsert_item(d.address, d.name)
            removed = self._drop_missing({d.address for d in devices})
            self.log(f"Scan found {len(devices)} display(s)."
                     + (f" Removed {removed} that weren't found." if removed else ""))
        elif event == "error":
            self.scan_btn.setEnabled(True)
            self.scan_btn.setText("Scan")
            self.log(data["message"])
        elif event == "connecting":
            self.log(f"Connecting to {label}…")
        elif event == "connected":
            info = data.get("info")
            self.log(f"Connected to {label}" + (f" ({info.width}×{info.height})" if info else ""))
        elif event == "connect_failed":
            self.log(f"Could not connect to {label}: {data['message']}")
        elif event == "disconnected":
            self.log(f"{label} disconnected" + ("" if data.get("expected") else " unexpectedly"))
        elif event == "reconnected":
            self.log(f"Re-established hold on {label}")
        elif event == "hold_changed":
            self.log(f"{'Holding' if data['hold'] else 'Released'} {label}")
        elif event in ("preset_ok", "command_ok"):
            if self.manager.rotation_running(addr):
                return  # the Rotation tab shows progress; don't flood the log
            what = f"preset “{data['preset']}”" if event == "preset_ok" else data["command"]
            self.log(f"{label}: {what} ✓")
        elif event == "command_failed":
            self.log(f"{label}: {data['command']} failed — {data['message']}")
        elif event == "upload_preparing":
            self.log(f"{label}: preparing {Path(data['name']).name}…")
        elif event == "upload_prepared":
            self.log(f"{label}: sending {data['summary']} (file was {data['source_kb']:.0f} KB)")
            self.upload_bar.setRange(0, 0)  # busy until the first window reports
            self.upload_bar.setVisible(True)
        elif event == "upload_progress":
            if data["total"]:
                self.upload_bar.setRange(0, data["total"])
                self.upload_bar.setValue(data["sent"])
                self.upload_bar.setFormat(f"{data['sent'] / 1024:.0f} / {data['total'] / 1024:.0f} KB  (%p%)")
            return
        elif event == "upload_finished":
            self.upload_bar.setVisible(False)
            if data.get("ok"):
                self.log(f"{label}: uploaded {data['kb']:.0f} KB in {data['seconds']:.0f} s")
        elif event == "_password_result":
            if data["error"]:
                self.log(f"{label}: password change failed – {data['error']}")
            else:
                dev = self.settings.find(addr)
                if dev:
                    dev.password = data["new"]
                    self.save_settings()
                self.pw_current.clear()
                self.pw_new.clear()
                self.log(f"{label}: password {'removed' if not data['new'] else 'saved on the display'}"
                         + ("" if dev else " (save the display under Security so the app remembers it)"))
        elif event == "group_unsynced":
            self.log(f"{label}: the panels play this animation on their own clocks, so they won't line up. "
                     "Security → Edit group… → tick “start animations together” to keep them in step.")
        elif event == "_upload_done":
            self._uploading.discard(addr)
        elif event == "rotation_stopped" and data.get("message"):
            self.log(f"{label}: rotation stopped – {data['message']}")
        if event.startswith("rotation_"):
            self.rotation_tab.on_event(event, data)
            if event == "rotation_step":
                return  # frequent; no need to re-render the device list
        if addr:
            self._upsert_item(addr)
        self._update_controls()

    def closeEvent(self, event) -> None:
        for tab in self._plugin_tabs:
            if hasattr(tab, "shutdown"):
                try:
                    tab.shutdown()
                except Exception:
                    log.exception("add-on shutdown failed")
        self.manager.shutdown()
        super().closeEvent(event)
