"""MQTT add-on for pypixelgui.

Installs alongside ``pypixelgui``. The GUI picks it up automatically and shows
an MQTT tab; ``pypixelgui-bridge`` runs it headless. Displays appear in Home
Assistant automatically through MQTT discovery.
"""

__version__ = "0.2.0"

from .bridge import MqttBridge, MqttSettings, parse_payload  # noqa: E402

__all__ = ["MqttBridge", "MqttSettings", "parse_payload", "__version__"]
