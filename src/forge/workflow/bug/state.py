"""Bug workflow state definition."""

from datetime import datetime
from typing import Any

from forge.models.workflow import TicketType
from forge.workflow.base import (
    BaseState,
    CIIntegrationState,
    PRIntegrationState,
    ReviewIntegrationState,
)


class BugState(
    BaseState, PRIntegrationState, CIIntegrationState, ReviewIntegrationState, total=False
):
    """State specific to Bug workflow."""

    ticket_type: TicketType

    # Bug-specific
    rca_content: str | None
    bug_fix_implemented: bool
    tdd_approach: bool

    # Documentation
    docs_pr_url: str | None

    # Q&A mode
    qa_history: list[dict[str, str]]  # List of {question, answer, artifact_type, timestamp}
    generation_context: dict[str, Any]  # Stored context from generation
    is_question: bool  # Current comment is a question (not feedback)


def create_initial_bug_state(ticket_key: str, **kwargs: Any) -> BugState:
    """Create initial state for a new Bug workflow run."""
    now = datetime.utcnow().isoformat()

    # Default values - can be overridden by kwargs
    defaults = {
        "thread_id": ticket_key,
        "ticket_key": ticket_key,
        "ticket_type": TicketType.BUG,
        "current_node": "start",
        "is_paused": False,
        "retry_count": 0,
        "last_error": None,
        "created_at": now,
        "updated_at": now,
        "rca_content": None,
        "bug_fix_implemented": False,
        "workspace_path": None,
        "pr_urls": [],
        "fork_owner": None,
        "fork_repo": None,
        "merge_conflicts": [],
        "local_review_attempts": 0,
        "tdd_approach": False,
        "ci_status": None,
        "current_pr_url": None,
        "current_pr_number": None,
        "current_repo": None,
        "repos_to_process": [],
        "repos_completed": [],
        "implemented_tasks": [],
        "current_task_key": None,
        "ci_failed_checks": [],
        "ci_fix_attempts": 0,
        "ci_skipped_checks": [],
        "ai_review_status": None,
        "ai_review_results": [],
        "human_review_status": None,
        "pr_merged": False,
        "feedback_comment": None,
        "revision_requested": False,
        "messages": [],
        "context": {},
        "qa_history": [],
        "generation_context": {},
        "is_question": False,
    }

    # Merge with kwargs, letting kwargs override defaults
    defaults.update(kwargs)

    return BugState(**defaults)
