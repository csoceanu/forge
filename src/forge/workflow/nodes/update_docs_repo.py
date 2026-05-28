"""Post-merge docs repo update node.

When forge.docs_repo is set, clones the separate docs repo after code merge,
runs the update agent in a container with both repos mounted, and creates a
fork-based PR for the docs changes. Non-blocking.
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
from forge.workflow.nodes.implementation import _clean_forge_gitignore
from forge.workflow.utils import update_state_timestamp
from forge.workspace.git_ops import GitOperations
from forge.workspace.manager import WorkspaceManager

logger = logging.getLogger(__name__)


async def update_docs_repo(state: WorkflowState) -> WorkflowState:
    """Update documentation in a separate docs repo after code merge.

    Checks forge.docs_repo project property. If not set, skips.
    If set, clones both repos, runs the update agent with the code repo
    mounted read-only, and creates a fork-based PR.

    Non-blocking: failures log a warning and proceed.

    Args:
        state: Current workflow state (after merge).

    Returns:
        Updated state with docs_pr_url if a PR was created.
    """
    ticket_key = state["ticket_key"]
    current_repo = state.get("current_repo", "")

    # Check for separate docs repo
    docs_repo = None
    try:
        project_key = extract_project_key(ticket_key)
        settings = get_settings()
        jira = JiraClient(settings)
        try:
            docs_repo = await jira.get_project_docs_repo(project_key)
        finally:
            await jira.close()
    except Exception as e:
        logger.warning(f"Could not check docs repo config for {ticket_key}: {e}")

    if not docs_repo or docs_repo == current_repo:
        logger.info(f"No separate docs repo for {ticket_key}, skipping")
        return state

    logger.info(f"Updating separate docs repo {docs_repo} for {ticket_key}")

    settings = get_settings()
    guardrails = state.get("context", {}).get("guardrails", "")
    branch_name = state.get("context", {}).get("branch_name", f"forge/{ticket_key.lower()}")
    fork_owner = state.get("fork_owner", "")
    fork_repo = state.get("fork_repo", "")

    try:
        # Clone upstream and checkout the fork branch (same pattern as workspace_setup.py)
        code_manager = WorkspaceManager(base_dir=settings.workspace_base_dir)
        code_workspace = code_manager.create_workspace(
            repo_name=current_repo,
            ticket_key=f"{ticket_key}-code-ref",
        )

        docs_manager = WorkspaceManager(base_dir=settings.workspace_base_dir)
        docs_workspace = docs_manager.create_workspace(
            repo_name=docs_repo,
            ticket_key=ticket_key,
            branch_name=branch_name,
        )

        try:
            # Clone upstream repo, add fork remote, checkout the PR branch
            # (same pattern as workspace_setup.py prepare_workspace)
            code_git = GitOperations(code_workspace)
            code_git.clone()
            code_git.add_fork_remote(fork_owner, fork_repo)
            code_git.checkout_branch(branch_name, remote="fork")

            # Clone and set up the docs repo
            docs_git = GitOperations(docs_workspace)
            docs_git.clone()
            docs_git.create_branch()

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

            # Run the doc update agent with both repos mounted
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
                task_key=f"{ticket_key}-docs-repo",
                repo_name=docs_repo,
                extra_mounts=[(code_workspace.path, "/code-repo")],
            )

            # Clean .forge/ from .gitignore before committing
            _clean_forge_gitignore(docs_workspace.path)

            # Commit any uncommitted changes
            if docs_git.has_uncommitted_changes():
                docs_git.stage_all()
                docs_git.commit(f"[{ticket_key}] docs: update documentation for code changes")

            # Check if any commits were made
            if not _branch_has_commits(docs_workspace.path):
                logger.info(f"No doc changes needed in {docs_repo} for {ticket_key}")
                return state

            # Create PR
            docs_pr_url = await _create_docs_pr(
                ticket_key=ticket_key,
                docs_repo=docs_repo,
                git=docs_git,
                branch_name=branch_name,
                settings=settings,
            )
            logger.info(f"Created docs PR for {ticket_key}: {docs_pr_url}")
            return update_state_timestamp({**state, "docs_pr_url": docs_pr_url})

        finally:
            docs_manager.destroy_workspace(docs_workspace)
            code_manager.destroy_workspace(code_workspace)

    except Exception as e:
        logger.warning(f"Separate docs repo update failed for {ticket_key}: {e}")
        return state


async def _create_docs_pr(
    ticket_key: str,
    docs_repo: str,
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
