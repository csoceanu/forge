import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def resolve_skill_paths(ticket_key: str, skills_dir: Path) -> list[str]:
    """Return ordered skill source paths for Deep Agents.

    Deep Agents loads sources in order and deduplicates by skill name,
    with later sources overriding earlier ones (last wins). Default comes
    first; project override comes last so it wins on name collision.
    """
    default_dir = skills_dir / "default"

    if "-" not in ticket_key:
        logger.info("Skills: default only (no ticket key)")
        return [str(default_dir) + "/"]

    project = ticket_key.split("-")[0].lower()
    override_dir = skills_dir / project

    if not override_dir.is_dir():
        logger.info(f"Skills: default only (no override for project '{project}')")
        return [str(default_dir) + "/"]

    logger.info(f"Skills: project override active for '{project}' ({override_dir})")
    return [str(default_dir) + "/", str(override_dir) + "/"]
