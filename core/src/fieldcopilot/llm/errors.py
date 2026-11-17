"""Errors every provider maps its own failures onto.

The reliability layer only needs to know one thing: is it worth trying again?
"""


class LLMError(Exception):
    retryable = False


class LLMTimeout(LLMError):
    retryable = True


class LLMRateLimited(LLMError):
    retryable = True

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class LLMUnavailable(LLMError):
    """Server errors and connection failures."""

    retryable = True


class LLMRefused(LLMError):
    """The model (and any server-side fallback) declined. Not retried."""


class LLMBadRequest(LLMError):
    """Our request was wrong. Retrying won't help; fix the code."""


class LLMBadOutput(LLMError):
    """The reply didn't match the schema we asked for."""


class BudgetExceeded(LLMError):
    """The tenant's or the run's token / cost budget is used up."""
