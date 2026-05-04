"""Bug workflow node for specialized bug fixing with RCA generation."""

import logging
from typing import Any

from langgraph.graph import END

from forge.config import get_settings
from forge.integrations.agents import ForgeAgent
from forge.integrations.jira.client import JiraClient
from forge.models.workflow import ForgeLabel
from forge.prompts import load_prompt
from forge.workflow.feature.state import FeatureState as WorkflowState
from forge.workflow.utils import set_paused, update_state_timestamp

logger = logging.getLogger(__name__)


async def analyze_bug(state: WorkflowState) -> WorkflowState:
    """Analyze a bug ticket and generate RCA.

    This node:
    1. Reads bug description from Jira
    2. Generates Root Cause Analysis using Claude
    3. Stores RCA in Jira and transitions for review

    Args:
        state: Current workflow state.

    Returns:
        Updated state with RCA content.
    """
    ticket_key = state["ticket_key"]

    logger.info(f"Analyzing bug {ticket_key}")

    settings = get_settings()
    jira = JiraClient(settings)
    agent = ForgeAgent(settings)

    try:
        # Get bug details
        issue = await jira.get_issue(ticket_key)
        bug_description = issue.description or ""
        bug_summary = issue.summary

        if not bug_description.strip():
            logger.warning(f"No description found for bug {ticket_key}")
            return {
                **state,
                "last_error": "No bug description provided",
                "current_node": "analyze_bug",
            }

        # Build context
        context: dict[str, Any] = {
            "ticket_key": ticket_key,
            "summary": bug_summary,
        }

        # Generate RCA using Deep Agents
        user_prompt = load_prompt(
            "analyze-bug",
            bug_summary=bug_summary,
            bug_description=bug_description,
        )

        rca_content = await agent.run_task(
            task="analyze-bug",
            prompt=user_prompt,
            context=context,
        )

        # Add RCA as comment to Jira
        await jira.add_comment(ticket_key, f"## Root Cause Analysis\n\n{rca_content}")

        # Set workflow label for RCA pending approval
        await jira.set_workflow_label(ticket_key, ForgeLabel.RCA_PENDING)

        logger.info(f"RCA generated for {ticket_key}")

        return update_state_timestamp(
            {
                **state,
                "rca_content": rca_content,
                "current_node": "rca_approval_gate",
                "last_error": None,
                "context": {**state.get("context", {}), **context},
            }
        )

    except Exception as e:
        logger.error(f"Bug analysis failed for {ticket_key}: {e}")
        return {
            **state,
            "last_error": str(e),
            "current_node": "analyze_bug",
            "retry_count": state.get("retry_count", 0) + 1,
        }
    finally:
        await jira.close()


def rca_approval_gate(state: WorkflowState) -> WorkflowState:
    """Pause workflow for RCA approval.

    Args:
        state: Current workflow state.

    Returns:
        State with is_paused=True.
    """
    ticket_key = state["ticket_key"]
    logger.info(f"RCA approval gate: pausing for {ticket_key}")

    return set_paused(state, "rca_approval_gate")


def route_rca_approval(state: WorkflowState) -> str:
    """Route based on RCA approval status.

    Args:
        state: Current workflow state.

    Returns:
        Next node name or END.
    """
    # Check if this is a question (Q&A mode) - check FIRST
    if state.get("is_question") and state.get("feedback_comment"):
        logger.info(f"Q&A mode: routing to answer_question for {state['ticket_key']}")
        return "answer_question"

    if state.get("revision_requested") and state.get("feedback_comment"):
        return "regenerate_rca"

    if state.get("is_paused"):
        logger.info(
            f"RCA approval: workflow paused for {state['ticket_key']}, waiting for approval webhook"
        )
        return END

    return "implement_bug_fix"


async def implement_bug_fix(state: WorkflowState) -> WorkflowState:
    """Implement bug fix using TDD approach.

    This node:
    1. First writes a failing test
    2. Then implements the fix
    3. Verifies the test passes

    Args:
        state: Current workflow state with RCA.

    Returns:
        Updated state after fix implementation.
    """
    ticket_key = state["ticket_key"]
    rca_content = state.get("rca_content", "")
    workspace_path = state.get("workspace_path")

    logger.info(f"Implementing TDD bug fix for {ticket_key}")

    # If no workspace, need to set one up
    if not workspace_path:
        # Route to workspace setup with bug context
        return update_state_timestamp(
            {
                **state,
                "current_node": "setup_workspace",
            }
        )

    settings = get_settings()
    agent = ForgeAgent(settings)

    try:
        # Generate test-first implementation using Deep Agents
        fix_prompt = load_prompt("fix-bug", rca_content=rca_content)

        result = await agent.run_task(
            task="implement-task",
            prompt=fix_prompt,
            context={
                "ticket_key": ticket_key,
                "workspace_path": workspace_path,
                "tdd_approach": True,
            },
        )

        # Apply changes
        from pathlib import Path

        from forge.workflow.nodes.implementation import _apply_code_changes
        from forge.workspace.git_ops import GitOperations
        from forge.workspace.manager import Workspace

        workspace = Workspace(
            path=Path(workspace_path),
            repo_name=state.get("current_repo", ""),
            branch_name=state.get("context", {}).get("branch_name", ""),
            ticket_key=ticket_key,
        )
        git = GitOperations(workspace)

        files_modified = _apply_code_changes(result, Path(workspace_path))

        if files_modified > 0:
            git.stage_all()
            git.commit(f"[{ticket_key}] Fix: {state.get('context', {}).get('summary', 'Bug fix')}")
            git.push()

        logger.info(f"Bug fix implemented for {ticket_key}")

        return update_state_timestamp(
            {
                **state,
                "bug_fix_implemented": True,
                "current_node": "create_pr",
            }
        )

    except Exception as e:
        logger.error(f"Bug fix implementation failed for {ticket_key}: {e}")
        return {
            **state,
            "last_error": str(e),
            "current_node": "implement_bug_fix",
            "retry_count": state.get("retry_count", 0) + 1,
        }


async def regenerate_rca(state: WorkflowState) -> WorkflowState:
    """Regenerate RCA based on feedback.

    Args:
        state: Current workflow state with feedback.

    Returns:
        Updated state with new RCA.
    """
    ticket_key = state["ticket_key"]
    feedback = state.get("feedback_comment", "")
    original_rca = state.get("rca_content", "")

    logger.info(f"Regenerating RCA for {ticket_key} with feedback")

    settings = get_settings()
    jira = JiraClient(settings)
    agent = ForgeAgent(settings)

    try:
        prompt = load_prompt(
            "regenerate-rca",
            original_rca=original_rca,
            feedback=feedback,
        )

        new_rca = await agent.run_task(
            task="analyze-bug",
            prompt=prompt,
            context={
                "ticket_key": ticket_key,
                "is_revision": True,
            },
        )

        # Update Jira
        await jira.add_comment(ticket_key, f"## Updated Root Cause Analysis\n\n{new_rca}")

        return update_state_timestamp(
            {
                **state,
                "rca_content": new_rca,
                "feedback_comment": None,
                "revision_requested": False,
                "current_node": "rca_approval_gate",
            }
        )

    except Exception as e:
        logger.error(f"RCA regeneration failed for {ticket_key}: {e}")
        return {
            **state,
            "last_error": str(e),
            "current_node": "regenerate_rca",
        }
    finally:
        await jira.close()
