"""MQTT add-on for pypixelgui.

Installs alongside ``pypixelgui``. The GUI picks it up automatically and shows
an MQTT tab; ``pypixelgui-bridge`` runs it headless.
"""

from .bridge import MqttBridge, MqttSettings, parse_payload

__all__ = ["MqttBridge", "MqttSettings", "parse_payload"]
__version__ = "0.1.0"
