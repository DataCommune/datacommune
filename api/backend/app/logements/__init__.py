"""Multi-logements detection and deduplication for FICHE-06 (Phase 48).

Pure Python only (D-10): floor parsing, deduplication (service.py) and
signal detection (detection.py) have no I/O, no DB access, and no
framework dependency, so every honesty rule is unit-testable offline.
"""
