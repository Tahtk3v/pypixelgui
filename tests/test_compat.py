import ssl

from pypixelgui import compat


def test_uses_certifi_only_when_store_is_empty(monkeypatch):
    certifi = __import__("pytest").importorskip("certifi")
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)

    class Empty:
        def cert_store_stats(self):
            return {"x509_ca": 0}

    monkeypatch.setattr(ssl, "create_default_context", lambda: Empty())
    compat.ensure_ca_certificates()
    import os
    assert os.environ["SSL_CERT_FILE"] == certifi.where()


def test_leaves_working_systems_alone(monkeypatch):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)

    class Full:
        def cert_store_stats(self):
            return {"x509_ca": 120}

    monkeypatch.setattr(ssl, "create_default_context", lambda: Full())
    compat.ensure_ca_certificates()
    import os
    assert "SSL_CERT_FILE" not in os.environ
