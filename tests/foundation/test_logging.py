from __future__ import annotations

import io
import logging


def test_redacting_filter_removes_credentials_and_bootstrap_secrets() -> None:
    from paper_reader.logging import RedactingFilter

    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.addFilter(RedactingFilter())
    logger = logging.getLogger("paper_reader.tests.redaction")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    logger.info(
        "Authorization: Bearer %s api_key=%s http://127.0.0.1/#bootstrap=%s",
        "bearer-value",
        "sk-example-secret",
        "launcher-secret",
    )

    logged = output.getvalue()
    assert "bearer-value" not in logged
    assert "sk-example-secret" not in logged
    assert "launcher-secret" not in logged
    assert logged.count("[REDACTED]") >= 3


def test_uvicorn_log_configuration_redacts_every_handler() -> None:
    from paper_reader.logging import build_uvicorn_log_config

    configuration = build_uvicorn_log_config()

    assert "redact" in configuration["filters"]
    assert all(
        "redact" in handler.get("filters", []) for handler in configuration["handlers"].values()
    )
    assert configuration["formatters"]["default"]["()"] == (
        "paper_reader.logging.RedactingDefaultFormatter"
    )
    assert configuration["formatters"]["access"]["()"] == (
        "paper_reader.logging.RedactingAccessFormatter"
    )


def test_redaction_preserves_uvicorn_access_record_arguments() -> None:
    from uvicorn.logging import AccessFormatter

    from paper_reader.logging import RedactingFilter

    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:1234", "GET", "/?api_key=secret-value", "1.1", 200),
        exc_info=None,
    )

    RedactingFilter().filter(record)
    rendered = AccessFormatter(fmt="%(message)s").format(record)

    assert "secret-value" not in rendered
    assert "[REDACTED]" in rendered
    assert "GET" in rendered


def test_log_records_reject_newline_terminal_and_extended_credential_injection() -> None:
    from paper_reader.logging import RedactingFilter, RedactingFormatter

    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.addFilter(RedactingFilter())
    handler.setFormatter(RedactingFormatter("%(levelname)s %(message)s"))
    logger = logging.getLogger("paper_reader.tests.log_injection")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    logger.info(
        "Authorization: Basic basic-secret\r\n"
        "Set-Cookie: paper_reader_session=session-secret; Path=/\n"
        "refresh_token=refresh-secret\u2028\x1b[31mFORGED "
        "OLLAMA_API_KEY=ollama-secret AWS_SECRET_ACCESS_KEY=aws-secret "
        "PAPER_READER_PRIVATE_KEY=private-secret"
    )

    logged = output.getvalue()
    for secret in (
        "basic-secret",
        "session-secret",
        "refresh-secret",
        "ollama-secret",
        "aws-secret",
        "private-secret",
    ):
        assert secret not in logged
    assert logged.count("\n") == 1
    assert "\r" not in logged
    assert "\u2028" not in logged
    assert "\x1b" not in logged
    assert "[REDACTED]" in logged


def test_exception_text_is_redacted_and_cannot_forge_a_second_log_record() -> None:
    from paper_reader.logging import RedactingFilter, RedactingFormatter

    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.addFilter(RedactingFilter())
    handler.setFormatter(RedactingFormatter("%(levelname)s %(message)s"))
    logger = logging.getLogger("paper_reader.tests.exception_injection")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.ERROR)

    try:
        raise RuntimeError("client_secret=exception-secret\nERROR forged-record")
    except RuntimeError:
        logger.exception("safe failure")

    logged = output.getvalue()
    assert "exception-secret" not in logged
    assert logged.count("\n") == 1
    assert "ERROR forged-record" in logged


def test_application_invocation_info_uses_the_redacting_server_handler() -> None:
    from paper_reader.logging import build_uvicorn_log_config

    configuration = build_uvicorn_log_config()
    assert configuration["loggers"]["paper_reader"] == {
        "handlers": ["default"],
        "level": "INFO",
        "propagate": False,
    }
    assert "redact" in configuration["handlers"]["default"]["filters"]
