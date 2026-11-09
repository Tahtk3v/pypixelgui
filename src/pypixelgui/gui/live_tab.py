"""Live tab: weather and Dutch public transport departures."""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from .. import presets as presets_mod
from ..live import transit, weather


class _Done(QObject):
    finished = Signal(object, object)  # (callback, result-or-exception)


class LiveTab(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self._done = _Done()
        self._done.finished.connect(lambda cb, res: cb(res))
        self._stops: list[transit.Stop] | None = None
        lay = QVBoxLayout(self)
        lay.addWidget(self._weather_box())
        lay.addWidget(self._transit_box())
        credit = QLabel("Weather data by <a href='https://open-meteo.com/'>Open-Meteo.com</a> (CC BY 4.0) · "
                        "departures by <a href='https://ovapi.nl/'>OVapi</a>")
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
        save = QPushButton("Save as preset…")
        save.clicked.connect(lambda: self._save(make_entry()))
        for wdg in (show, keep, every, save):
            row.addWidget(wdg)
        row.addStretch()
        return row

    def _preset(self, entry):
        return presets_mod.user_preset(entry) if entry else None

    def _show(self, entry) -> None:
        if entry:
            self.window._run_preset(self._preset(entry))

    def _keep(self, entry, minutes: int) -> None:
        addr = self.window.current_address()
        if not entry or not addr or not self.window.manager.is_connected(addr):
            if entry:
                self.window.log("Connect to a display first.")
            return
        self.window.manager.start_rotation(addr, [(self._preset(entry), minutes * 60)], loop=True)
        self.window.log(f"Showing “{entry['name']}”, refreshed every {minutes} min (stop it on the Rotation tab)")
        self.window.refresh_device_list()

    def _save(self, entry) -> None:
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
        opts.addWidget(self.days)
        opts.addWidget(self.lang)
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
                                       "lang": self.lang.currentText()}]]}

    # --- transit -----------------------------------------------------------------------

    def _transit_box(self) -> QGroupBox:
        box = QGroupBox("Public transport departures (Netherlands – bus, tram, metro, ferry)")
        form = QFormLayout(box)
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
        opts.addWidget(self.lines)
        opts.addWidget(self.count)
        opts.addWidget(self.stop_label)
        form.addRow("Show", self._wrap(opts))
        form.addRow(self._wrap(self._actions(self._transit_entry)))
        return box

    def _find_stop(self) -> None:
        q = self.stop_q.text().strip()
        if not q:
            return
        self.stop_list.clear()
        if self._stops is None:
            self.stop_list.addItem("Downloading the list of Dutch stops (once)…")

        def work():
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
                                       "lines": lines, "count": self.count.value()}]]}

    @staticmethod
    def _wrap(layout) -> QWidget:
        layout.setContentsMargins(0, 0, 0, 0)
        w = QWidget()
        w.setLayout(layout)
        return w
