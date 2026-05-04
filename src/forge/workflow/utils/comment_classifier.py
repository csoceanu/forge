"""Comment classification for Forge Q&A mode."""

import re
from enum import StrEnum


class CommentType(StrEnum):
    """Type of comment detected in Jira comments."""

    QUESTION = "question"
    FEEDBACK = "feedback"


# Pattern for @forge ask (case insensitive)
_FORGE_ASK_PATTERN = re.compile(r"^\s*@forge\s+ask", re.IGNORECASE)

# Pattern for question mark at start (allowing leading whitespace)
_QUESTION_MARK_PATTERN = re.compile(r"^\s*\?")


def classify_comment(comment_text: str) -> CommentType:
    """Classify a Jira comment into question or feedback.

    Classification rules:
    - Questions: Comments starting with '?' or '@forge ask' (case-insensitive)
    - Feedback: Everything else (default)

    Approvals are handled exclusively via label changes (forge:*-approved),
    not via comment text.

    Args:
        comment_text: The text of the comment to classify.

    Returns:
        The classified comment type.
    """
    # Handle empty or whitespace-only comments
    if not comment_text or not comment_text.strip():
        return CommentType.FEEDBACK

    # Check for question markers first (takes precedence)
    if _QUESTION_MARK_PATTERN.match(comment_text):
        return CommentType.QUESTION

    if _FORGE_ASK_PATTERN.match(comment_text):
        return CommentType.QUESTION

    # Default to feedback
    return CommentType.FEEDBACK
