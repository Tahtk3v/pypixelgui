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


def test_rotation_resolve(tmp_path):
    import pytest
    from pypixelgui import rotation
    entry = {"name": "Shop", "items": [{"preset": "cyber", "seconds": 5}, {"preset": "Trans flag"}]}
    items = rotation.resolve(entry)
    assert [(p.name, s) for p, s in items] == [("CYBER", 5.0), ("Trans flag", 10.0)]
    with pytest.raises(KeyError):
        rotation.resolve({"name": "x", "items": [{"preset": "nope"}]})
    s = Settings(rotations=[entry])
    assert Settings.load(s.save(tmp_path / "c.json")).rotations == [entry]
