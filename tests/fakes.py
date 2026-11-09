import asyncio
from types import SimpleNamespace


class FakeClient:
    """Stands in for pypixelcolor.AsyncClient."""
    instances: list["FakeClient"] = []
    fail_connect = False

    def __init__(self, address):
        self.address = address
        self.calls = []
        self._session = SimpleNamespace(is_connected=False)
        FakeClient.instances.append(self)

    async def connect(self):
        if FakeClient.fail_connect:
            raise OSError("device not found")
        self._session.is_connected = True

    async def disconnect(self):
        self._session.is_connected = False

    def get_device_info(self):
        return SimpleNamespace(width=32, height=32, led_type=2, password_flag=255)

    def __getattr__(self, name):
        async def call(**kwargs):
            await asyncio.sleep(0)
            self.calls.append((name, kwargs))
        return call
