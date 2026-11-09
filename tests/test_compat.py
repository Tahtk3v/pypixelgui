import ssl

from pypixelgui import compat


def _no_truststore(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "truststore", None)  # import fails → fallback path


def test_prefers_the_os_trust_store(monkeypatch):
    import sys
    import types
    calls = []
    fake = types.SimpleNamespace(inject_into_ssl=lambda: calls.append(1))
    monkeypatch.setitem(sys.modules, "truststore", fake)
    assert compat.ensure_ca_certificates() == "system trust store" and calls == [1]


def test_uses_certifi_only_when_store_is_empty(monkeypatch):
    certifi = __import__("pytest").importorskip("certifi")
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    _no_truststore(monkeypatch)

    class Empty:
        def cert_store_stats(self):
            return {"x509_ca": 0}

    monkeypatch.setattr(ssl, "create_default_context", lambda: Empty())
    compat.ensure_ca_certificates()
    import os
    assert os.environ["SSL_CERT_FILE"] == certifi.where()


def test_leaves_working_systems_alone(monkeypatch):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    _no_truststore(monkeypatch)

    class Full:
        def cert_store_stats(self):
            return {"x509_ca": 120}

    monkeypatch.setattr(ssl, "create_default_context", lambda: Full())
    compat.ensure_ca_certificates()
    import os
    assert "SSL_CERT_FILE" not in os.environ
