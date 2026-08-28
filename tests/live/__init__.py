"""Real-model smoke tests (section 17.4 of ``doc/项目设计方案.md``).

Every test in this package is skipped unless ``RUN_LIVE_TESTS=1`` is set, and it is
excluded from ``make test`` and ``make check``. Credentials are read from the
environment only and are never written to a fixture, a log or a report.
"""
