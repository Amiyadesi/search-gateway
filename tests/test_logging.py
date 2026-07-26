from app.config import Settings
from app.utils import logging as logging_utils


def test_logging_disables_backtrace_locals(monkeypatch):
    added = {}

    monkeypatch.setattr(logging_utils, "get_settings", lambda: Settings(log_level="INFO"))
    monkeypatch.setattr(logging_utils.logger, "remove", lambda: None)

    def fake_add(*args, **kwargs):
        added.update(kwargs)

    monkeypatch.setattr(logging_utils.logger, "add", fake_add)

    logging_utils.configure_logging()

    assert added["backtrace"] is False
    assert added["diagnose"] is False
