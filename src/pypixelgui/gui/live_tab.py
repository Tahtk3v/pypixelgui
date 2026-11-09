"""Live tab: weather and Dutch public transport departures."""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from .. import presets as presets_mod
from ..live import events, ns, schedule, transit, weather


class _Done(QObject):
    finished = Signal(object, object)  # (callback, result-or-exception)


class LiveTab(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self._done = _Done()
        self._done.finished.connect(lambda cb, res: cb(res))
        self._stops: list[transit.Stop] | None = None
        ns.set_api_key(window.settings.api_keys.get("ns", ""))
        lay = QVBoxLayout(self)
        lay.addWidget(self._weather_box())
        lay.addWidget(self._transit_box())
        lay.addWidget(self._events_box())
        credit = QLabel("Weather data by <a href='https://open-meteo.com/'>Open-Meteo.com</a> (CC BY 4.0) · "
                        "departures by <a href='https://ovapi.nl/'>OVapi</a> and "
                        "<a href='https://apiportal.ns.nl/'>NS</a>")
        credit.setOpenExternalLinks(True)
        lay.addWidget(credit)
        lay.addStretch()

    # --- helpers ------------------------------------------------------------------

    def _background(self, work, then) -> None:
        """Run ``work()`` off the UI thread, then ``then(result_or_exception)`` on it."""
        def run():
            try:
                res = work()
            except Exception as e:  # handed to the UI as a value
                res = e
            self._done.finished.emit(then, res)
        threading.Thread(target=run, daemon=True).start()

    def _actions(self, make_entry) -> QHBoxLayout:
        row = QHBoxLayout()
        show = self.window._send_row("Show now", lambda: self._show(make_entry()))
        every = QSpinBox(minimum=1, maximum=240, value=5, suffix=" min")
        keep = self.window._send_row("Keep updated every", lambda: self._keep(make_entry(), every.value()))
        every.setToolTip("How often it checks for changes (also for the saved preset)")
        save = QPushButton("Save as preset…")
        save.clicked.connect(lambda: self._save(make_entry(), every.value()))
        for wdg in (show, keep, every, save):
            row.addWidget(wdg)
        row.addStretch()
        return row

    def _preset(self, entry):
        return presets_mod.user_preset(entry) if entry else None

    def _show(self, entry) -> None:
        if entry:
            self.window._run_preset(self._preset(entry))

    @staticmethod
    def _with_refresh(entry, minutes: int):
        if entry:
            entry["steps"][0][1]["refresh"] = int(minutes) * 60
        return entry

    def _keep(self, entry, minutes: int) -> None:
        entry = self._with_refresh(entry, minutes)
        if entry:
            self.window._run_preset(self._preset(entry))
            self.window.refresh_device_list()

    def _save(self, entry, minutes: int) -> None:
        entry = self._with_refresh(entry, minutes)
        if not entry:
            return
        name, ok = QInputDialog.getText(self, "Save preset", "Preset name:", text=entry["name"])
        if not ok or not name.strip():
            return
        entry["name"] = name.strip()
        self.window.settings.presets = [e for e in self.window.settings.presets
                                        if e["name"].lower() != entry["name"].lower()] + [entry]
        self.window.save_settings()
        self.window._populate_presets()
        self.window.log(f"Saved preset “{entry['name']}” – it fetches fresh data every time it's shown")

    # --- weather -------------------------------------------------------------------

    def _weather_box(self) -> QGroupBox:
        box = QGroupBox("Weather")
        form = QFormLayout(box)
        row = QHBoxLayout()
        self.city = QLineEdit(placeholderText="City or town, e.g. Utrecht")
        self.city.returnPressed.connect(self._find_city)
        find = QPushButton("Search")
        find.clicked.connect(self._find_city)
        row.addWidget(self.city)
        row.addWidget(find)
        form.addRow("Place", self._wrap(row))
        self.places = QComboBox()
        form.addRow("", self.places)
        opts = QHBoxLayout()
        self.days = QSpinBox(minimum=0, maximum=3, value=2, suffix=" days forecast")
        self.lang = QComboBox()
        self.lang.addItems(["nl", "en"])
        self.show_place = QLineEdit(placeholderText="Label (optional)")
        self.w_speed = self._speed_combo()
        self.w_layout = self._layout_combo()
        opts.addWidget(self.days)
        opts.addWidget(self.lang)
        opts.addWidget(self.w_speed)
        opts.addWidget(self.w_layout)
        opts.addWidget(self.show_place)
        form.addRow("Show", self._wrap(opts))
        form.addRow(self._wrap(self._actions(self._weather_entry)))
        return box

    def _find_city(self) -> None:
        q = self.city.text().strip()
        if not q:
            return
        self.places.clear()
        self.places.addItem("Searching…")

        def done(res):
            self.places.clear()
            if isinstance(res, Exception):
                self.window.log(f"Weather search failed: {res}")
                return
            if not res:  # retry worldwide
                self.window.log("No Dutch match – searching worldwide")
                self._background(lambda: weather.search_places(q, country=None), done_world)
                return
            for p in res:
                self.places.addItem(p.label(), p)

        def done_world(res):
            if isinstance(res, Exception) or not res:
                self.window.log(f"No place found for “{q}”")
                return
            for p in res:
                self.places.addItem(p.label(), p)
        self._background(lambda: weather.search_places(q), done)

    def _weather_entry(self) -> dict | None:
        place = self.places.currentData()
        if not isinstance(place, weather.Place):
            self.window.log("Search for a place first.")
            return None
        label = self.show_place.text().strip()
        return {"name": f"Weer {place.name}", "description": f"Weather for {place.label()}",
                "steps": [["weather", {"latitude": place.latitude, "longitude": place.longitude,
                                       "label": label, "forecast_days": self.days.value(),
                                       "lang": self.lang.currentText(),
                                       "speed": self.w_speed.currentData(),
                                       "layout": self.w_layout.currentData()}]]}

    # --- transit -----------------------------------------------------------------------

    def _transit_box(self) -> QGroupBox:
        box = QGroupBox("Public transport departures (Netherlands)")
        form = QFormLayout(box)
        src = QHBoxLayout()
        self.t_source = QComboBox()
        self.t_source.addItem("Bus, tram, metro, ferry (OVapi)", "ovapi")
        self.t_source.addItem("Trains with platforms (NS – needs a free API key)", "ns")
        self.t_source.currentIndexChanged.connect(lambda *_: self.stop_list.clear())
        self.ns_key = QLineEdit(self.window.settings.api_keys.get("ns", ""), echoMode=QLineEdit.Password,
                                placeholderText="NS API key (apiportal.ns.nl → Ns-App)")
        self.ns_key.editingFinished.connect(self._save_ns_key)
        src.addWidget(self.t_source)
        src.addWidget(self.ns_key)
        form.addRow("Source", self._wrap(src))
        row = QHBoxLayout()
        self.stop_q = QLineEdit(placeholderText="Stop name and/or town, e.g. Vaartsche Rijn Utrecht")
        self.stop_q.returnPressed.connect(self._find_stop)
        find = QPushButton("Search")
        find.clicked.connect(self._find_stop)
        row.addWidget(self.stop_q)
        row.addWidget(find)
        form.addRow("Stop", self._wrap(row))
        self.stop_list = QListWidget()
        self.stop_list.setMaximumHeight(110)
        form.addRow("", self.stop_list)
        opts = QHBoxLayout()
        self.lines = QLineEdit(placeholderText="Only lines (e.g. 12, 28) – empty = all")
        self.count = QSpinBox(minimum=1, maximum=8, value=3, suffix=" departures")
        self.stop_label = QLineEdit(placeholderText="Label (optional)")
        self.t_speed = self._speed_combo()
        self.t_layout = self._layout_combo()
        opts.addWidget(self.lines)
        opts.addWidget(self.count)
        opts.addWidget(self.t_speed)
        opts.addWidget(self.t_layout)
        opts.addWidget(self.stop_label)
        form.addRow("Show", self._wrap(opts))
        form.addRow(self._wrap(self._actions(self._transit_entry)))
        return box

    def _find_stop(self) -> None:
        q = self.stop_q.text().strip()
        if not q:
            return
        self.stop_list.clear()
        use_ns = self.t_source.currentData() == "ns"
        if use_ns and not ns.has_key():
            self.window.log("Enter your NS API key first (free at apiportal.ns.nl).")
            return
        if self._stops is None and not use_ns:
            self.stop_list.addItem("Downloading the list of Dutch stops (once)…")

        def work():
            if use_ns:
                return ns.search_stations(q)
            if self._stops is None:
                self._stops = transit.load_stops()
            return transit.search_stops(self._stops, q)

        def done(res):
            self.stop_list.clear()
            if isinstance(res, Exception):
                self.window.log(f"Stop search failed: {res}")
                return
            if not res:
                self.stop_list.addItem(f"No stop matches “{q}”")
            for s in res:
                it = QListWidgetItem(s.label())
                it.setData(0x0100, s)
                self.stop_list.addItem(it)
        self._background(work, done)

    def _transit_entry(self) -> dict | None:
        it = self.stop_list.currentItem()
        stop = it.data(0x0100) if it else None
        if not isinstance(stop, transit.Stop):
            self.window.log("Search for a stop and select it first.")
            return None
        lines = [x.strip() for x in self.lines.text().replace(";", ",").split(",") if x.strip()]
        return {"name": f"Vertrek {stop.name}", "description": f"Departures from {stop.label()}",
                "steps": [["transit", {"stop": stop.code, "label": self.stop_label.text().strip(),
                                       "lines": lines, "count": self.count.value(),
                                       "source": self.t_source.currentData(),
                                       "speed": self.t_speed.currentData(),
                                       "layout": self.t_layout.currentData()}]]}

    def _save_ns_key(self) -> None:
        key = self.ns_key.text().strip()
        if key == self.window.settings.api_keys.get("ns", ""):
            return
        self.window.settings.api_keys["ns"] = key
        ns.set_api_key(key)
        self.window.save_settings()

    # --- event schedules ---------------------------------------------------------------

    def _events_box(self) -> QGroupBox:
        box = QGroupBox("Event schedule (BornHack, eth0, or any frab/pretalx schedule)")
        form = QFormLayout(box)
        row = QHBoxLayout()
        self.event_kind = QComboBox()
        for name in events.KNOWN:
            self.event_kind.addItem(name, name)
        self.event_kind.addItem("Other (schedule.xml URL)", "custom")
        self.event_kind.currentIndexChanged.connect(self._event_kind_changed)
        self.editions = QComboBox()
        self.editions.setMinimumWidth(160)
        find = QPushButton("Find editions")
        find.clicked.connect(self._find_editions)
        row.addWidget(self.event_kind)
        row.addWidget(self.editions)
        row.addWidget(find)
        form.addRow("Event", self._wrap(row))
        custom = QHBoxLayout()
        self.event_url = QLineEdit(placeholderText="https://…/schedule.xml")
        self.event_site = QLineEdit(placeholderText="Website or icon URL (for the icon)")
        custom.addWidget(self.event_url)
        custom.addWidget(self.event_site)
        self.custom_row = self._wrap(custom)
        self.custom_row.setVisible(False)
        form.addRow("", self.custom_row)
        opts = QHBoxLayout()
        self.rooms = QLineEdit(placeholderText="Only rooms (comma-separated) – empty = all")
        pick_rooms = QPushButton("Rooms…")
        pick_rooms.setToolTip("Pick rooms from the schedule – handy at Congress, which has hundreds")
        pick_rooms.clicked.connect(self._pick_rooms)
        opts.addWidget(pick_rooms)
        self.e_count = QSpinBox(minimum=1, maximum=10, value=4, suffix=" talks")
        self.e_lang = QComboBox()
        self.e_lang.addItems(["en", "nl"])
        self.e_speed = self._speed_combo()
        self.e_layout = self._layout_combo()
        for w in (self.rooms, self.e_count, self.e_lang, self.e_speed, self.e_layout):
            opts.addWidget(w)
        form.addRow("Show", self._wrap(opts))
        form.addRow(self._wrap(self._actions(self._event_entry)))
        return box

    def _schedule_url(self) -> str | None:
        if self.event_kind.currentData() == "custom":
            return self.event_url.text().strip() or None
        ed = self.editions.currentData()
        return ed.schedule_url if isinstance(ed, events.Edition) else None

    def _pick_rooms(self) -> None:
        url = self._schedule_url()
        if not url:
            self.window.log("Choose an edition (or paste a schedule URL) first.")
            return
        self.window.log("Loading the schedule to list its rooms…")

        def done(res):
            if isinstance(res, Exception):
                self.window.log(f"Could not load the schedule: {res}")
                return
            counts: dict[str, int] = {}
            for t in res.talks:
                counts[t.room] = counts.get(t.room, 0) + 1
            if not counts:
                self.window.log("This schedule has no talks yet.")
                return
            chosen = {r.strip().lower() for r in self.rooms.text().split(",") if r.strip()}
            dlg = RoomDialog(self, counts, chosen)
            if dlg.exec():
                self.rooms.setText(", ".join(dlg.selected()))
        self._background(lambda: schedule.fetch(url), done)

    def _event_kind_changed(self) -> None:
        custom = self.event_kind.currentData() == "custom"
        self.custom_row.setVisible(custom)
        self.editions.setVisible(not custom)
        self.editions.clear()

    def _find_editions(self) -> None:
        kind = self.event_kind.currentData()
        if kind == "custom":
            return
        self.editions.clear()
        self.editions.addItem("Searching…")

        def done(res):
            self.editions.clear()
            if isinstance(res, Exception):
                self.window.log(f"Could not list {kind} editions: {res}")
                return
            for e in res:
                self.editions.addItem(e.name, e)
            if not res:
                self.window.log(f"No {kind} schedules found")
        self._background(events.KNOWN[kind], done)

    def _event_entry(self) -> dict | None:
        rooms = [x.strip() for x in self.rooms.text().split(",") if x.strip()]
        if self.event_kind.currentData() == "custom":
            url = self.event_url.text().strip()
            if not url:
                self.window.log("Paste the event's schedule.xml URL first.")
                return None
            name, icon = url.split("/")[2] if "//" in url else url, self.event_site.text().strip()
        else:
            ed = self.editions.currentData()
            if not isinstance(ed, events.Edition):
                self.window.log("Click “Find editions” and pick one first.")
                return None
            url, name, icon = ed.schedule_url, ed.name, ed.icon_url
            name = name.split(" (")[0]  # "39C3 (2025)" → "39C3": short labels scroll less
        return {"name": f"Schedule {name}", "description": f"What's on at {name}",
                "steps": [["schedule", {"url": url, "icon": icon, "label": "" if self.event_kind.currentData()
                                        == "custom" else name, "rooms": rooms, "count": self.e_count.value(),
                                        "lang": self.e_lang.currentText(), "speed": self.e_speed.currentData(),
                                        "layout": self.e_layout.currentData()}]]}

    @staticmethod
    def _layout_combo() -> QComboBox:
        combo = QComboBox()
        combo.setToolTip("Auto: one scrolling line on 16-pixel-high displays, rows on taller ones")
        for label, key in (("Auto layout", "auto"), ("One line", "line"), ("Rows", "rows")):
            combo.addItem(label, key)
        return combo

    @staticmethod
    def _speed_combo() -> QComboBox:
        combo = QComboBox()
        combo.setToolTip("How fast text scrolls when it doesn't fit the display")
        for label, key in (("Slow scroll", "slow"), ("Normal scroll", "normal"), ("Fast scroll", "fast")):
            combo.addItem(label, key)
        combo.setCurrentIndex(0)  # slow reads best on small panels
        return combo

    @staticmethod
    def _wrap(layout) -> QWidget:
        layout.setContentsMargins(0, 0, 0, 0)
        w = QWidget()
        w.setLayout(layout)
        return w


class RoomDialog(QDialog):
    """Checklist of a schedule's rooms (busiest first), with a filter box."""

    def __init__(self, parent, counts: dict[str, int], chosen: set[str]):
        super().__init__(parent)
        self.setWindowTitle("Rooms to show")
        self.resize(420, 480)
        lay = QVBoxLayout(self)
        self.filter = QLineEdit(placeholderText="Filter rooms…")
        self.filter.textChanged.connect(self._apply_filter)
        lay.addWidget(self.filter)
        self.list = QListWidget()
        for room, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            it = QListWidgetItem(f"{room}  ({n})")
            it.setData(0x0100, room)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if room.lower() in chosen else Qt.Unchecked)
            self.list.addItem(it)
        lay.addWidget(self.list)
        note = QLabel("Nothing ticked = all rooms.")
        lay.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _apply_filter(self, text: str) -> None:
        t = text.lower()
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(bool(t) and t not in it.data(0x0100).lower())

    def selected(self) -> list[str]:
        return [self.list.item(i).data(0x0100) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]
