"""Container sandbox configuration."""

from dataclasses import dataclass, field


@dataclass
class SandboxConfig:
    """Configuration for sandbox execution environment.

    This configuration is used by the orchestrator to control
    container behavior and resource limits.
    """

    # Container image
    image: str = "forge-dev:latest"

    # Resource limits
    memory_limit: str = "4g"
    cpu_limit: str = "2"
    timeout_seconds: int = 1800  # 30 minutes

    # Network configuration
    # slirp4netns: rootless networking with limited connectivity
    # none: no network access
    # host: full host network access (not recommended)
    network_mode: str = "slirp4netns"

    # Test execution
    skip_tests: bool = False
    max_test_retries: int = 3

    # Debugging
    preserve_workspace_on_failure: bool = False

    # Additional environment variables
    env_vars: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "SandboxConfig":
        """Create config from environment variables."""
        import os

        return cls(
            image=os.getenv("FORGE_SANDBOX_IMAGE", "forge-dev:latest"),
            memory_limit=os.getenv("FORGE_SANDBOX_MEMORY", "4g"),
            cpu_limit=os.getenv("FORGE_SANDBOX_CPUS", "2"),
            timeout_seconds=int(os.getenv("FORGE_SANDBOX_TIMEOUT", "1800")),
            network_mode=os.getenv("FORGE_SANDBOX_NETWORK", "slirp4netns"),
            skip_tests=os.getenv("FORGE_SANDBOX_SKIP_TESTS", "").lower() == "true",
            max_test_retries=int(os.getenv("FORGE_SANDBOX_MAX_RETRIES", "3")),
            preserve_workspace_on_failure=os.getenv("FORGE_SANDBOX_PRESERVE_ON_FAILURE", "").lower()
            == "true",
        )
