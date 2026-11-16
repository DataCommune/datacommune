"""Success-ratio gate must exclude legitimate 404s for cadastre and BAN too.

Same regression class as DVF (tests/etl/test_dvf_gate.py): a department absent
from the source returns 404 and produces no file, and must not count against
the swap success ratio.  A 5xx/timeout still counts (it raises, not a 404).
"""

import pytest

from app.etl_ban.pipeline import BanPipeline
from app.etl_cadastre.pipeline import CadastrePipeline

pytestmark = pytest.mark.unit


# 5 configured departments so a single 404 (20%) stays under the 40% guard.
_SCOPE = ("44", "85", "49", "72", "53")


@pytest.fixture
def _scope(bind_departements):
    bind_departements(_SCOPE, CadastrePipeline, BanPipeline)


class TestCadastreGate:
    def test_before_download_returns_theoretical(self, _scope):
        assert CadastrePipeline().expected_file_count == 5

    def test_not_found_excluded(self, _scope):
        p = CadastrePipeline()
        p._not_found_count = 1  # one department absent at source (20% <= 40%)
        assert p.expected_file_count == 4

    def test_outage_keeps_strict_denominator(self, _scope):
        p = CadastrePipeline()
        p._not_found_count = 3  # 3/5 = 60% > 40% guard -> full denominator
        assert p.expected_file_count == 5


class TestBanGate:
    def test_before_download_returns_theoretical(self, _scope):
        assert BanPipeline().expected_file_count == 5

    def test_not_found_excluded(self, _scope):
        p = BanPipeline()
        p._not_found_count = 1  # 20% <= 40%
        assert p.expected_file_count == 4

    def test_outage_keeps_strict_denominator(self, _scope):
        p = BanPipeline()
        p._not_found_count = 3  # 60% > 40% guard
        assert p.expected_file_count == 5
