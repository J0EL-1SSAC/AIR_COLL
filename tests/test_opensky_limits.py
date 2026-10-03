import logging

from backend.airwatch.collector import log_daily_quota_warning
from backend.airwatch.opensky import retry_after_seconds


def test_quota_warning_only_when_estimate_exceeds_configured_limit(caplog):
    with caplog.at_level(logging.WARNING):
        estimate = log_daily_quota_warning(30, 4000, 1)
        assert not caplog.records
        estimate = log_daily_quota_warning(20, 4000, 1)
    assert estimate == 4320
    assert any("may exceed OpenSky's daily credit quota" in record.message for record in caplog.records)


def test_retry_after_header_is_optional_and_validated():
    assert retry_after_seconds("45") == 45
    assert retry_after_seconds(None) is None
    assert retry_after_seconds("not-provided") is None
    assert retry_after_seconds("0") is None
