"""Fail closed when a required disposable-DB selection did not execute fully."""

import pytest


class _RequiredContractGate:
    """Track execution independently from optional terminal/reporting plugins."""

    def __init__(self) -> None:
        self.executed: set[str] = set()
        self.omitted: set[str] = set()

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        if report.skipped:
            self.omitted.add(report.nodeid)

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        # Pytest reports xfail (including run=False), setup and teardown skips
        # as skipped outcomes. A collected item alone proves no execution.
        if report.skipped:
            self.omitted.add(report.nodeid)
        if report.when == "call":
            self.executed.add(report.nodeid)

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        if (
            not self.executed
            or len(self.executed) != session.testscollected
            or self.omitted
        ):
            reporter = session.config.pluginmanager.get_plugin("terminalreporter")
            if reporter is not None:
                reporter.write_sep(
                    "=",
                    f"Required contract gate: {len(self.omitted)} omitted; "
                    f"{len(self.executed)} executed; "
                    f"{session.testscollected} collected",
                    red=True,
                )
            session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_configure(config: pytest.Config) -> None:
    config.pluginmanager.register(
        _RequiredContractGate(), "required-contract-execution"
    )
