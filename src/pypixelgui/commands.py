"""Command schema shared by the GUI and the MQTT bridge.

Every command that reaches a display goes through :func:`validate`, so the
GUI and remote callers (MQTT) obey the same rules. Remote callers are held to
a stricter subset: no local file paths, no custom fonts, and destructive
commands only when explicitly allowed.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from typing import Any, Callable

_HEX_RE = re.compile(r"^#?[0-9a-fA-F]{6}$")


class CommandError(ValueError):
    """Raised when a command or its parameters are not acceptable."""


# --- coercers -------------------------------------------------------------

def _int(lo: int | None = None, hi: int | None = None) -> Callable[[Any], int]:
    def coerce(v: Any) -> int:
        if isinstance(v, bool):
            raise CommandError("expected an integer, got a boolean")
        try:
            i = int(v)
        except (TypeError, ValueError):
            raise CommandError(f"expected an integer, got {v!r}") from None
        if lo is not None and i < lo or hi is not None and i > hi:
            raise CommandError(f"{i} is out of range [{lo}, {hi}]")
        return i
    return coerce


def _bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    s = str(v).strip().lower()
    if s in ("1", "true", "on", "yes"):
        return True
    if s in ("0", "false", "off", "no"):
        return False
    raise CommandError(f"expected a boolean, got {v!r}")


def _color(v: Any) -> str:
    s = str(v).strip()
    if not _HEX_RE.match(s):
        raise CommandError(f"expected a hex colour like 'ff8800', got {v!r}")
    return s.lstrip("#").lower()


def _opt_color(v: Any) -> str | None:
    return None if v in (None, "", "none", "null") else _color(v)


def _text(max_len: int) -> Callable[[Any], str]:
    def coerce(v: Any) -> str:
        s = str(v)
        if not s:
            raise CommandError("text must not be empty")
        if len(s) > max_len:
            raise CommandError(f"text longer than {max_len} characters")
        return s
    return coerce


def _choice(*options: str) -> Callable[[Any], str]:
    def coerce(v: Any) -> str:
        s = str(v).strip().lower()
        if s not in options:
            raise CommandError(f"expected one of {', '.join(options)}, got {v!r}")
        return s
    return coerce


def _str(v: Any) -> str:
    return str(v)


# --- schema ---------------------------------------------------------------

@dataclass(frozen=True)
class CommandSpec:
    params: dict[str, Callable[[Any], Any]]
    required: tuple[str, ...] = ()
    #: Parameter used when a remote caller sends a bare value instead of JSON.
    primary: str | None = None
    #: Wipes or deletes stored content on the device.
    destructive: bool = False
    #: Parameters only the local GUI may set (e.g. file paths).
    local_only: frozenset[str] = field(default_factory=frozenset)
    #: Whole command is local-only (reads files on this machine).
    local_command: bool = False


ANIMATIONS = ("static", "scroll_left", "scroll_right", "scroll_up", "scroll_down",
              "blink", "fade", "snowflake", "laser")

COMMANDS: dict[str, CommandSpec] = {
    "send_text": CommandSpec(
        params={
            "text": _text(500),
            "color": _color,
            "bg_color": _opt_color,
            "animation": _choice(*ANIMATIONS),
            "speed": _int(0, 100),
            "rainbow_mode": _int(0, 9),
            "save_slot": _int(0, 255),
            "font": _str,
            "char_height": _int(1, 64),
        },
        required=("text",),
        primary="text",
        local_only=frozenset({"font"}),
    ),
    "send_image": CommandSpec(
        params={"path": _str, "resize_method": _choice("crop", "fit"), "save_slot": _int(0, 255)},
        required=("path",),
        local_command=True,
    ),
    "send_image_b64": CommandSpec(
        params={
            "data": _str,
            "ext": _choice(".png", ".gif", ".jpg", ".jpeg", ".bmp", ".webp"),
            "resize_method": _choice("crop", "fit"),
            "save_slot": _int(0, 255),
        },
        required=("data", "ext"),
    ),
    "set_brightness": CommandSpec(params={"level": _int(0, 100)}, required=("level",), primary="level"),
    "set_power": CommandSpec(params={"on": _bool}, required=("on",), primary="on"),
    "set_orientation": CommandSpec(params={"orientation": _int(0, 3)}, required=("orientation",),
                                   primary="orientation"),
    "set_clock_mode": CommandSpec(
        params={"style": _int(0, 8), "show_date": _bool, "format_24": _bool},
        primary="style",
    ),
    "set_fun_mode": CommandSpec(params={"enable": _bool}, primary="enable"),
    "set_pixel": CommandSpec(params={"x": _int(0, 511), "y": _int(0, 127), "color": _color},
                             required=("x", "y", "color")),
    "set_scores": CommandSpec(params={"score_p1": _int(0, 999), "score_p2": _int(0, 999)}),
    "set_timer": CommandSpec(params={"action": _choice("start", "pause", "stop")}, required=("action",),
                             primary="action"),
    "set_time": CommandSpec(params={"hour": _int(0, 23), "minute": _int(0, 59), "second": _int(0, 59)}),
    "show_slot": CommandSpec(params={"number": _int(0, 255)}, required=("number",), primary="number"),
    "delete": CommandSpec(params={"n": _int(0, 255)}, required=("n",), primary="n", destructive=True),
    "clear": CommandSpec(params={}, destructive=True),
}


def validate(name: str, params: dict[str, Any] | None = None, *, remote: bool = False,
             allow_destructive: bool = True) -> dict[str, Any]:
    """Check a command and return cleaned keyword arguments.

    Args:
        name: Command name (a key of :data:`COMMANDS`).
        params: Raw parameters.
        remote: True when the request did not come from the local GUI.
        allow_destructive: Whether ``clear``/``delete`` are permitted.
    """
    spec = COMMANDS.get(name)
    if spec is None:
        raise CommandError(f"unknown command {name!r}")
    if remote and spec.local_command:
        raise CommandError(f"{name!r} is only available from the local GUI")
    if spec.destructive and not allow_destructive:
        raise CommandError(f"{name!r} is destructive and disabled")
    params = dict(params or {})
    out: dict[str, Any] = {}
    for key, value in params.items():
        if key not in spec.params:
            raise CommandError(f"{name}: unknown parameter {key!r}")
        if remote and key in spec.local_only:
            raise CommandError(f"{name}: parameter {key!r} is only available from the local GUI")
        try:
            out[key] = spec.params[key](value)
        except CommandError as e:
            raise CommandError(f"{name}.{key}: {e}") from None
    missing = [k for k in spec.required if k not in out]
    if missing:
        raise CommandError(f"{name}: missing parameter(s) {', '.join(missing)}")
    return out


def to_client_call(name: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Translate a validated command into a pypixelcolor client method call."""
    if name == "send_image_b64":
        try:
            raw = base64.b64decode(kwargs.pop("data"), validate=True)
        except ValueError:
            raise CommandError("send_image_b64: 'data' is not valid base64") from None
        if len(raw) > 2 * 1024 * 1024:
            raise CommandError("send_image_b64: image larger than 2 MiB")
        return "send_image_hex", {"hex_string": raw.hex(), "file_extension": kwargs.pop("ext"), **kwargs}
    return name, kwargs
