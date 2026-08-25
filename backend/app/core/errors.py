"""Application wide error hierarchy.

Each external integration raises its own error type so callers (and tests)
can react precisely without string matching.
"""

from __future__ import annotations


class RCABotError(Exception):
    """Base class for all RCA Bot errors."""

    def safe_message(self) -> str:
        """Human-readable message suitable for Slack/users (no internals)."""
        return str(self) or type(self).__name__


# --- Slack -----------------------------------------------------------------


class SlackError(RCABotError):
    pass


class SlackAuthenticationError(SlackError):
    pass


class SlackAPIError(SlackError):
    pass


class SlackThreadNotFoundError(SlackError):
    pass


class ChannelNotConfiguredError(SlackError):
    pass


# --- Sentry ----------------------------------------------------------------


class SentryError(RCABotError):
    pass


class SentryAuthenticationError(SentryError):
    pass


class SentryIssueNotFoundError(SentryError):
    pass


class SentryEventNotFoundError(SentryError):
    pass


class SentryAPIError(SentryError):
    pass


class InvalidSentryURLError(SentryError):
    pass


class MissingSentryReferenceError(SentryError):
    """Raised when no Sentry URL exists in the Slack thread (V1 never guesses)."""


# Historical/alternate name kept as an alias.
MissingSentryURLWarning = MissingSentryReferenceError


class SentryNotFoundError(SentryError):
    """Base class: referenced Sentry issue or event does not exist."""



# --- Repository / ingestion -------------------------------------------------


class RepositoryError(RCABotError):
    pass


class RepositoryNotFoundError(RepositoryError):
    pass


class IndexingError(RepositoryError):
    pass


# --- Retrieval ---------------------------------------------------------------


class RetrievalError(RCABotError):
    pass


class EmbeddingError(RetrievalError):
    pass


class VectorStoreError(RetrievalError):
    pass


# --- LLM ---------------------------------------------------------------------


class LLMError(RCABotError):
    pass


class LLMUnavailableError(LLMError):
    pass


class LLMResponseError(LLMError):
    pass


class LLMMalformedOutputError(LLMResponseError):
    """The LLM replied but its output could not be parsed into the schema."""


# --- Investigation ------------------------------------------------------------


class ValidationError_(RCABotError):
    pass


class DatabaseError(RCABotError):
    pass
