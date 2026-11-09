"""Small platform fixes applied at start-up."""

from __future__ import annotations

import logging
import os
import ssl

log = logging.getLogger(__name__)


def ensure_ca_certificates() -> None:
    """Make HTTPS work where Python has no CA certificates.

    The python.org installer for macOS ships without root certificates until
    "Install Certificates.command" is run, so every HTTPS download fails with
    CERTIFICATE_VERIFY_FAILED. pypixelcolor downloads emoji pictures over HTTPS,
    so emoji in text silently disappear. If the default store is empty, point
    OpenSSL at certifi's bundle (OpenSSL reads SSL_CERT_FILE whenever a default
    context is created). Systems that already have certificates are left alone.
    """
    if os.environ.get("SSL_CERT_FILE"):
        return
    try:
        if ssl.create_default_context().cert_store_stats().get("x509_ca", 0) > 0:
            return
        import certifi
    except Exception:
        return
    os.environ["SSL_CERT_FILE"] = certifi.where()
    log.info("No system CA certificates found; using certifi's bundle for HTTPS")
