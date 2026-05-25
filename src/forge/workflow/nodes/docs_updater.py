"""Documentation update node — finds and fixes stale docs before PR creation.

Supports two modes:
- Same-repo: updates docs in the code workspace (Phase 1)
- Separate-repo: clones a dedicated docs repo, updates it, and creates a PR (Phase 2)

The mode is determined by the forge.docs_repo Jira project property.
"""

import logging
import subprocess
from pathlib import Path

from forge.config import Settings, get_settings
from forge.integrations.github.client import GitHubClient
from forge.integrations.jira.client import JiraClient
from forge.prompts import load_prompt
from forge.sandbox import ContainerRunner
from forge.skills.utils import extract_project_key
from forge.workflow.feature.state import FeatureState as WorkflowState
from forge.workflow.utils import update_state_timestamp
from forge.workspace.git_ops import GitOperations
from forge.workspace.manager import Workspace, WorkspaceManager

logger = logging.getLogger(__name__)


async def update_documentation(state: WorkflowState) -> WorkflowState:
    """Find and update documentation files that became stale due to code changes.

    Runs after local code review but before PR creation. Checks if the project
    has a separate docs repo configured (forge.docs_repo). If so, clones the
    docs repo, runs the update agent with the code diff, and creates a separate
    PR. If not, updates docs in the code workspace.

    Non-blocking: failures log a warning and proceed to PR creation.

    Args:
        state: Current workflow state.

    Returns:
        Updated state routing to create_pr.
    """
    ticket_key = state["ticket_key"]
    workspace_path = state.get("workspace_path")

    if not workspace_path:
        logger.info(f"No workspace for doc update on {ticket_key}, skipping")
        return update_state_timestamp({**state, "current_node": "create_pr"})

    logger.info(f"Running documentation update for {ticket_key}")

    settings = get_settings()
    current_repo = state.get("current_repo", "")

    # Check for separate docs repo
    docs_repo = None
    try:
        project_key = extract_project_key(ticket_key)
        jira = JiraClient(settings)
        try:
            docs_repo = await jira.get_project_docs_repo(project_key)
        finally:
            await jira.close()
    except Exception as e:
        logger.warning(f"Could not check docs repo config for {ticket_key}: {e}")

    if docs_repo and docs_repo != current_repo:
        return await _update_separate_docs_repo(state, docs_repo)
    else:
        return await _update_same_repo_docs(state)


async def _update_same_repo_docs(state: WorkflowState) -> WorkflowState:
    """Update docs in the same repo as the code (Phase 1 behavior)."""
    ticket_key = state["ticket_key"]
    workspace_path = state.get("workspace_path")
    settings = get_settings()
    guardrails = state.get("context", {}).get("guardrails", "")
    current_repo = state.get("current_repo", "")
    branch_name = state.get("context", {}).get("branch_name", "")

    task_description = load_prompt(
        "update-docs",
        workspace_path=workspace_path,
        guardrails=guardrails[:2000] if guardrails else "",
    )

    try:
        runner = ContainerRunner(settings)
        result = await runner.run(
            workspace_path=Path(workspace_path),
            task_summary="Update stale documentation",
            task_description=task_description,
            ticket_key=ticket_key,
            task_key=f"{ticket_key}-docs",
            repo_name=current_repo,
        )

        git = GitOperations(
            Workspace(
                path=Path(workspace_path),
                repo_name=current_repo,
                branch_name=branch_name,
                ticket_key=ticket_key,
            )
        )

        if git.has_uncommitted_changes():
            git.stage_all()
            git.commit(f"[{ticket_key}] docs: update documentation for code changes")
            logger.info(f"Committed doc updates for {ticket_key}")

        if result.success:
            logger.info(f"Documentation update completed for {ticket_key}")
        else:
            logger.warning(
                f"Documentation update container exited with errors for {ticket_key}, "
                f"proceeding to PR creation"
            )

        return update_state_timestamp(
            {
                **state,
                "current_node": "create_pr",
                "last_error": None,
            }
        )

    except Exception as e:
        logger.warning(f"Documentation update failed for {ticket_key}: {e}")
        return update_state_timestamp(
            {
                **state,
                "current_node": "create_pr",
                "last_error": None,
            }
        )


async def _update_separate_docs_repo(
    state: WorkflowState, docs_repo: str
) -> WorkflowState:
    """Update docs in a separate repository and create a PR.

    Clones the docs repo, runs the update agent in a container with both
    repos mounted — the docs repo as the workspace and the code repo as a
    read-only mount. The agent can git diff the code, read the handoff,
    and edit docs directly.
    """
    ticket_key = state["ticket_key"]
    workspace_path = state.get("workspace_path")
    settings = get_settings()
    guardrails = state.get("context", {}).get("guardrails", "")
    branch_name = state.get("context", {}).get("branch_name", f"forge/{ticket_key.lower()}")

    logger.info(f"Updating separate docs repo {docs_repo} for {ticket_key}")

    try:
        # Create a separate workspace for the docs repo
        manager = WorkspaceManager(base_dir=settings.workspace_base_dir)
        docs_workspace = manager.create_workspace(
            repo_name=docs_repo,
            ticket_key=ticket_key,
            branch_name=branch_name,
        )

        try:
            # Clone and set up the docs repo
            git = GitOperations(docs_workspace)
            git.clone()
            git.create_branch()

            # Add .forge/ to .gitignore (same pattern as workspace_setup.py)
            forge_dir = docs_workspace.path / ".forge"
            forge_dir.mkdir(exist_ok=True)
            gitignore_path = docs_workspace.path / ".gitignore"
            if gitignore_path.exists():
                content = gitignore_path.read_text()
                if ".forge" not in content:
                    if not content.endswith("\n"):
                        content += "\n"
                    content += "\n.forge/\n"
                    gitignore_path.write_text(content)
            else:
                gitignore_path.write_text(".forge/\n")

            # Run the doc update agent with both repos mounted:
            # - /workspace = docs repo (read-write, agent edits here)
            # - /code-repo = code repo (read-only, agent diffs here)
            task_description = load_prompt(
                "update-docs-separate",
                workspace_path=str(docs_workspace.path),
                guardrails=guardrails[:2000] if guardrails else "",
            )

            runner = ContainerRunner(settings)
            await runner.run(
                workspace_path=docs_workspace.path,
                task_summary="Update stale documentation in docs repo",
                task_description=task_description,
                ticket_key=ticket_key,
                task_key=f"{ticket_key}-docs-separate",
                repo_name=docs_repo,
                extra_mounts=[(Path(workspace_path), "/code-repo")],
            )

            # Clean .forge/ from .gitignore before committing
            # (same pattern as implementation.py)
            from forge.workflow.nodes.implementation import _clean_forge_gitignore

            _clean_forge_gitignore(docs_workspace.path)

            # Commit any uncommitted changes
            if git.has_uncommitted_changes():
                git.stage_all()
                git.commit(f"[{ticket_key}] docs: update documentation for code changes")

            # Check if any commits were made on the branch
            has_changes = _branch_has_commits(docs_workspace.path)

            if has_changes:
                docs_pr_url = await _create_docs_pr(
                    ticket_key=ticket_key,
                    docs_repo=docs_repo,
                    _docs_workspace=docs_workspace,
                    git=git,
                    branch_name=branch_name,
                    settings=settings,
                )
                logger.info(f"Created docs PR for {ticket_key}: {docs_pr_url}")
                return update_state_timestamp(
                    {
                        **state,
                        "current_node": "create_pr",
                        "docs_pr_url": docs_pr_url,
                        "last_error": None,
                    }
                )
            else:
                logger.info(f"No doc changes needed in {docs_repo} for {ticket_key}")
                return update_state_timestamp(
                    {**state, "current_node": "create_pr", "last_error": None}
                )

        finally:
            manager.destroy_workspace(docs_workspace)

    except Exception as e:
        logger.warning(
            f"Separate docs repo update failed for {ticket_key}: {e}"
        )
        return update_state_timestamp(
            {
                **state,
                "current_node": "create_pr",
                "last_error": None,
            }
        )


async def _create_docs_pr(
    ticket_key: str,
    docs_repo: str,
    _docs_workspace: Workspace,
    git: GitOperations,
    branch_name: str,
    settings: Settings,
) -> str:
    """Create a fork-based PR for the docs repo."""
    owner, repo = docs_repo.split("/")

    github = GitHubClient(settings)
    jira = JiraClient(settings)
    try:
        fork_data = await github.get_or_create_fork(owner, repo)
        fork_owner = fork_data["owner"]["login"]
        fork_repo = fork_data["name"]

        await github.sync_fork_with_upstream(fork_owner, fork_repo)
        git.add_fork_remote(fork_owner, fork_repo)
        git.push_to_fork()

        pr_data = await github.create_pull_request(
            owner=owner,
            repo=repo,
            title=f"[{ticket_key}] docs: update documentation for code changes",
            body=(
                f"Automated documentation update for {ticket_key}.\n\n"
                f"Code changes in the source repository made some documentation "
                f"files stale. This PR updates them to reflect the current code."
            ),
            head=f"{fork_owner}:{branch_name}",
            base="main",
        )

        pr_url = pr_data.get("html_url", "")

        await jira.add_comment(
            ticket_key,
            f"Documentation PR created: [{docs_repo}#{pr_data.get('number')}]({pr_url})",
        )

        return pr_url
    finally:
        await github.close()
        await jira.close()


def _branch_has_commits(workspace_path: Path) -> bool:
    """Check if the current branch has commits ahead of origin/main."""
    try:
        result = subprocess.run(
            ["git", "log", "origin/main..HEAD", "--oneline"],
            cwd=workspace_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


