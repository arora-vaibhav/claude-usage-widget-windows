import logging

from claude_usage.logging_setup import LOG_FILENAME, setup_logging


def _drain(logger):
    for h in list(logger.handlers):
        h.close()
        logger.removeHandler(h)


def test_setup_logging_writes_to_file(tmp_path):
    logger = setup_logging(str(tmp_path), level=logging.INFO)
    try:
        logger.warning("marker-xyzzy")
        for h in logger.handlers:
            h.flush()
        log_file = tmp_path / LOG_FILENAME
        assert log_file.exists()
        assert "marker-xyzzy" in log_file.read_text(encoding="utf-8")
    finally:
        _drain(logger)  # don't leak the handler into other tests


def test_setup_logging_is_idempotent(tmp_path):
    from logging.handlers import RotatingFileHandler

    logger = setup_logging(str(tmp_path))
    logger2 = setup_logging(str(tmp_path))
    try:
        assert logger is logger2
        file_handlers = [h for h in logger.handlers if isinstance(h, RotatingFileHandler)]
        assert len(file_handlers) == 1  # no duplicate handlers
    finally:
        _drain(logger)
