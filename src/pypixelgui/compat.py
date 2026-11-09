"""Small platform fixes applied at start-up."""

from __future__ import annotations

import logging
import os
import ssl

log = logging.getLogger(__name__)


def ensure_ca_certificates() -> str:
    """Make HTTPS certificate checks work the way the rest of the computer does.

    Python ships its own idea of trusted certificates, separate from the
    operating system's. On macOS (python.org installer, some Homebrew setups)
    it often has none, and behind security software or company networks it
    lacks the extra root certificates the OS trusts – both give
    CERTIFICATE_VERIFY_FAILED for weather lookups and emoji downloads.

    Preferred fix: ``truststore`` makes Python use the OS trust store (Keychain
    on macOS, the Windows certificate store, the system bundle on Linux).
    Fallback: certifi's bundle when Python's own store is empty.
    Returns which method is in use, for logging.
    """
    try:
        import truststore
        truststore.inject_into_ssl()
        return "system trust store"
    except Exception as e:  # not installed, or unsupported platform/Python
        log.debug("truststore unavailable: %s", e)
    if os.environ.get("SSL_CERT_FILE"):
        return "SSL_CERT_FILE"
    try:
        if ssl.create_default_context().cert_store_stats().get("x509_ca", 0) > 0:
            return "python default"
        import certifi
    except Exception:
        return "python default"
    os.environ["SSL_CERT_FILE"] = certifi.where()
    log.info("No system CA certificates found; using certifi's bundle for HTTPS")
    return "certifi"
