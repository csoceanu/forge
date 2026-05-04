"""Guardrails loader for reading constitution and agent guidelines."""

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Standard paths for guardrail files
CONSTITUTION_PATHS = [
    "CONSTITUTION.md",
    "constitution.md",
    ".claude/CONSTITUTION.md",
    "docs/CONSTITUTION.md",
]

AGENTS_PATHS = [
    "AGENTS.md",
    "agents.md",
    ".claude/AGENTS.md",
    "docs/AGENTS.md",
    "CLAUDE.md",
    ".claude/CLAUDE.md",
]


@dataclass
class Guardrails:
    """Loaded guardrails for code execution."""

    constitution: str | None
    agents: str | None
    repo_path: Path

    @property
    def has_constitution(self) -> bool:
        """Check if constitution is loaded."""
        return self.constitution is not None

    @property
    def has_agents(self) -> bool:
        """Check if agents guidelines are loaded."""
        return self.agents is not None

    def get_system_context(self) -> str:
        """Get combined guardrails as system context.

        Returns:
            Combined constitution and agent guidelines.
        """
        parts = []

        if self.constitution:
            parts.append("# Project Constitution\n\n" + self.constitution)

        if self.agents:
            parts.append("# Agent Guidelines\n\n" + self.agents)

        return "\n\n---\n\n".join(parts) if parts else ""


class GuardrailsLoader:
    """Loads guardrails from repository files."""

    def __init__(self, repo_path: Path):
        """Initialize the guardrails loader.

        Args:
            repo_path: Path to the repository root.
        """
        self.repo_path = repo_path

    def load(self, require_guardrails: bool = False) -> Guardrails:
        """Load guardrails from the repository.

        Args:
            require_guardrails: If True, raise error when guardrails missing.

        Returns:
            Loaded Guardrails instance.

        Raises:
            ValueError: If require_guardrails is True and no guardrails found.
        """
        repo_name = self.repo_path.name
        constitution = self._load_file(CONSTITUTION_PATHS)
        agents = self._load_file(AGENTS_PATHS)

        if constitution:
            logger.info(f"[{repo_name}] Loaded constitution ({len(constitution)} chars)")
        else:
            logger.warning(
                f"[{repo_name}] No constitution.md found. "
                "AI may operate without project-specific constraints. "
                f"Consider adding CONSTITUTION.md to {self.repo_path}"
            )

        if agents:
            logger.info(f"[{repo_name}] Loaded agents guidelines ({len(agents)} chars)")
        else:
            logger.warning(
                f"[{repo_name}] No agents.md/CLAUDE.md found. "
                "Using default agent behavior. "
                f"Consider adding AGENTS.md or CLAUDE.md to {self.repo_path}"
            )

        guardrails = Guardrails(
            constitution=constitution,
            agents=agents,
            repo_path=self.repo_path,
        )

        # Optionally block execution when guardrails are missing
        if require_guardrails and not guardrails.has_constitution and not guardrails.has_agents:
            raise ValueError(
                f"[{repo_name}] No guardrails found and require_guardrails=True. "
                "Refusing to execute code without any constraints."
            )

        return guardrails

    def _load_file(self, candidate_paths: list[str]) -> str | None:
        """Load the first existing file from candidate paths.

        Args:
            candidate_paths: List of paths to try.

        Returns:
            File contents if found, None otherwise.
        """
        for rel_path in candidate_paths:
            full_path = self.repo_path / rel_path
            if full_path.exists() and full_path.is_file():
                try:
                    content = full_path.read_text(encoding="utf-8")
                    logger.debug(f"Loaded guardrails from {rel_path}")
                    return content
                except Exception as e:
                    logger.warning(f"Failed to read {rel_path}: {e}")

        return None

    def validate_guardrails(self, guardrails: Guardrails) -> list[str]:
        """Validate loaded guardrails.

        Args:
            guardrails: Guardrails to validate.

        Returns:
            List of validation warnings.
        """
        warnings = []

        if not guardrails.has_constitution:
            warnings.append("No constitution found. Code execution may lack constraints.")

        if not guardrails.has_agents:
            warnings.append("No agents guidelines found. Using default behavior.")

        if guardrails.constitution and len(guardrails.constitution) < 100:
            warnings.append("Constitution is very short. Consider expanding guidelines.")

        return warnings
