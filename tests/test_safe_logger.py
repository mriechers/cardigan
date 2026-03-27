"""Tests for SafeLogger — extra key collision prevention (Bug #25)."""

import logging

from api.services.logging import SafeLogger, setup_logging


class TestSafeLogger:
    """Verify SafeLogger renames colliding extra keys."""

    def setup_method(self):
        """Install SafeLogger and create a test logger."""
        logging.setLoggerClass(SafeLogger)
        self.logger = logging.getLogger(f"test.safe_logger.{id(self)}")
        self.logger.setLevel(logging.DEBUG)
        self.records: list[logging.LogRecord] = []
        handler = logging.Handler()
        handler.emit = self.records.append  # type: ignore[assignment]
        self.logger.addHandler(handler)

    def test_colliding_key_filename_no_error(self):
        """extra={'filename': ...} must not raise KeyError."""
        self.logger.info("test message", extra={"filename": "test.txt"})
        assert len(self.records) == 1

    def test_colliding_key_renamed(self):
        """Colliding 'filename' should become 'extra_filename' on the record."""
        self.logger.info("test", extra={"filename": "test.txt"})
        record = self.records[0]
        assert record.extra_filename == "test.txt"  # type: ignore[attr-defined]

    def test_non_colliding_key_unchanged(self):
        """Non-reserved keys pass through with their original name."""
        self.logger.info("test", extra={"project_id": "abc123"})
        record = self.records[0]
        assert record.project_id == "abc123"  # type: ignore[attr-defined]
        assert not hasattr(record, "extra_project_id")

    def test_multiple_collisions(self):
        """Multiple reserved keys all get prefixed."""
        extras = {"filename": "a.txt", "lineno": 999, "module": "foo"}
        self.logger.info("test", extra=extras)
        record = self.records[0]
        assert record.extra_filename == "a.txt"  # type: ignore[attr-defined]
        assert record.extra_lineno == 999  # type: ignore[attr-defined]
        assert record.extra_module == "foo"  # type: ignore[attr-defined]

    def test_mixed_keys(self):
        """Mix of colliding and non-colliding keys handled correctly."""
        extras = {"filename": "x.txt", "custom_field": "ok"}
        self.logger.info("test", extra=extras)
        record = self.records[0]
        assert record.extra_filename == "x.txt"  # type: ignore[attr-defined]
        assert record.custom_field == "ok"  # type: ignore[attr-defined]


class TestSetupLoggingSetsClass:
    """Verify setup_logging installs SafeLogger as the logger class."""

    def test_logger_class_set(self):
        setup_logging(level="WARNING", enable_console=False)
        assert logging.getLoggerClass() is SafeLogger
