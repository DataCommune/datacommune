"""MCP server for agents (D-143): a thin envelope over the /v1 routes.

``protocole``: JSON-RPC over stateless HTTP, mounted at ``/mcp``.
``canal``: the in-process subrequest that replays a /v1 route.
``contrat``: the single projection of a /v1 value into a ``Mesure`` (five states).
``outils``: the six tools.
"""
