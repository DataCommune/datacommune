"""Shared settings of the outbound HTTP clients served on a request path.

``httpx.AsyncClient()`` builds its own TLS context unless given one, which
loads the whole CA bundle synchronously on the event loop: 18 ms per client on
python:3.12-slim, about 240 ms on the Windows workstation (2026-11-13). The
upstream readers of an address report open about thirty clients at once, so
each client of a request path passes ``verify=CONTEXTE_TLS`` instead. The
context is built once, with httpx's own defaults (certifi, or
``SSL_CERT_FILE``/``SSL_CERT_DIR``), and an ``ssl.SSLContext`` is safe to
share between connections.
"""

import httpx

CONTEXTE_TLS = httpx.create_ssl_context()
