"""Light / dark / follow-the-system appearance."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory

THEMES = ("system", "light", "dark")
_native_style: str | None = None


def _dark_palette() -> QPalette:
    p = QPalette()
    base, window, text = QColor(30, 30, 32), QColor(45, 45, 48), QColor(230, 230, 230)
    disabled = QColor(120, 120, 120)
    accent = QColor(64, 132, 214)
    for role, colour in (
        (QPalette.Window, window), (QPalette.WindowText, text), (QPalette.Base, base),
        (QPalette.AlternateBase, window), (QPalette.ToolTipBase, base), (QPalette.ToolTipText, text),
        (QPalette.Text, text), (QPalette.Button, window), (QPalette.ButtonText, text),
        (QPalette.BrightText, QColor(255, 80, 80)), (QPalette.Link, QColor(110, 170, 255)),
        (QPalette.Highlight, accent), (QPalette.HighlightedText, QColor(255, 255, 255)),
        (QPalette.PlaceholderText, disabled), (QPalette.Mid, QColor(70, 70, 74)),
        (QPalette.Dark, QColor(20, 20, 22)), (QPalette.Light, QColor(80, 80, 84)),
    ):
        p.setColor(role, colour)
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, disabled)
    p.setColor(QPalette.Disabled, QPalette.Button, QColor(38, 38, 40))
    return p


def apply(app: QApplication, theme: str) -> None:
    """Switch the whole app's look. ``system`` restores the platform's own style."""
    global _native_style
    if _native_style is None:
        _native_style = app.style().objectName()
    theme = theme if theme in THEMES else "system"
    if theme == "system":
        app.setStyle(QStyleFactory.create(_native_style) or QStyleFactory.create("Fusion"))
        app.setPalette(app.style().standardPalette())
        return
    fusion = QStyleFactory.create("Fusion")
    app.setStyle(fusion)
    app.setPalette(_dark_palette() if theme == "dark" else fusion.standardPalette())
