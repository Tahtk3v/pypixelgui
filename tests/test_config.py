from pypixelgui.config import DeviceEntry, Settings


def test_roundtrip_and_unique_aliases(tmp_path):
    s = Settings()
    s.upsert(DeviceEntry(address="A", name="LED Sign"))
    s.upsert(DeviceEntry(address="B", name="LED Sign"))
    s.plugins["mqtt"] = {"username": "u"}
    path = s.save(tmp_path / "c.json")
    loaded = Settings.load(path)
    assert [d.alias for d in loaded.devices] == ["led_sign", "led_sign_2"]
    assert loaded.plugins["mqtt"]["username"] == "u"
