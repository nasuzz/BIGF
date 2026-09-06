"""Log error categories and operation identifiers without retaining exceptions.

Exception messages, arguments, causes, and tracebacks can contain credentials.
Application error boundaries must not pass those objects to logging handlers.
"""

from __future__ import annotations

import logging


def error_type_name(error: BaseException) -> str:
    """Return a diagnostic category without formatting the exception object."""
    return type(error).__name__


def log_failure(
    logger: logging.Logger,
    error: BaseException,
    *,
    stage: str,
    request_id: str | None = None,
    question_id: str | None = None,
    step_id: str | None = None,
    level: int = logging.ERROR,
) -> None:
    """Emit only the error type and explicitly supplied operation identifiers."""
    fields = {"stage": stage, "error_type": error_type_name(error)}
    for key, value in (
        ("request_id", request_id),
        ("question_id", question_id),
        ("step_id", step_id),
    ):
        if value is not None:
            fields[key] = value
    logger.log(
        level,
        "operation_failed " + " ".join(f"{key}=%s" for key in fields),
        *fields.values(),
        exc_info=False,
        stack_info=False,
    )
