"""Graceful shutdown handling for workers and services."""

import asyncio
import contextlib
import logging
import signal
from collections.abc import Callable, Coroutine
from typing import Any

logger = logging.getLogger(__name__)


class GracefulShutdown:
    """Manager for graceful shutdown of async services.

    Handles SIGTERM and SIGINT signals, coordinating cleanup
    of running tasks and resources.
    """

    def __init__(self, timeout: float = 30.0) -> None:
        """Initialize shutdown manager.

        Args:
            timeout: Maximum seconds to wait for cleanup.
        """
        self.timeout = timeout
        self._shutdown_event = asyncio.Event()
        self._cleanup_tasks: list[Callable[[], Coroutine[Any, Any, None]]] = []
        self._running_tasks: set[asyncio.Task[Any]] = set()
        self._signal_received = False

    @property
    def is_shutting_down(self) -> bool:
        """Check if shutdown has been initiated.

        Returns:
            True if shutdown in progress.
        """
        return self._shutdown_event.is_set()

    def register_cleanup(
        self,
        cleanup_fn: Callable[[], Coroutine[Any, Any, None]],
    ) -> None:
        """Register an async cleanup function.

        Args:
            cleanup_fn: Async function to call during shutdown.
        """
        self._cleanup_tasks.append(cleanup_fn)

    def register_task(self, task: asyncio.Task[Any]) -> None:
        """Register a running task to track.

        Args:
            task: Task to track for shutdown.
        """
        self._running_tasks.add(task)
        task.add_done_callback(self._running_tasks.discard)

    async def wait_for_shutdown(self) -> None:
        """Wait until shutdown signal is received."""
        await self._shutdown_event.wait()

    def trigger_shutdown(self) -> None:
        """Trigger shutdown programmatically."""
        if not self._signal_received:
            self._signal_received = True
            logger.info("Shutdown triggered")
            self._shutdown_event.set()

    def _signal_handler(
        self,
        signum: int,
        _frame: Any,
    ) -> None:
        """Handle shutdown signal.

        Args:
            signum: Signal number.
            _frame: Current stack frame (unused).
        """
        sig_name = signal.Signals(signum).name
        logger.info(f"Received {sig_name}, initiating graceful shutdown")
        self.trigger_shutdown()

    def install_signal_handlers(self) -> None:
        """Install signal handlers for SIGTERM and SIGINT."""
        try:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(
                    sig,
                    lambda s=sig: self._signal_handler(s, None),
                )
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            signal.signal(signal.SIGTERM, self._signal_handler)
            signal.signal(signal.SIGINT, self._signal_handler)

    async def shutdown(self) -> None:
        """Execute graceful shutdown sequence.

        1. Cancel running tasks
        2. Wait for tasks to complete (with timeout)
        3. Run cleanup functions
        """
        logger.info("Starting graceful shutdown sequence")

        # Cancel all running tasks
        for task in self._running_tasks:
            if not task.done():
                task.cancel()

        # Wait for tasks to complete
        if self._running_tasks:
            logger.info(f"Waiting for {len(self._running_tasks)} tasks to complete")
            try:
                await asyncio.wait_for(
                    asyncio.gather(
                        *self._running_tasks,
                        return_exceptions=True,
                    ),
                    timeout=self.timeout,
                )
            except TimeoutError:
                logger.warning(f"Timeout waiting for tasks after {self.timeout}s")

        # Run cleanup functions
        for cleanup_fn in self._cleanup_tasks:
            try:
                logger.debug(f"Running cleanup: {cleanup_fn.__name__}")
                await asyncio.wait_for(
                    cleanup_fn(),
                    timeout=5.0,
                )
            except TimeoutError:
                logger.warning(f"Cleanup timeout: {cleanup_fn.__name__}")
            except Exception as e:
                logger.error(f"Cleanup error in {cleanup_fn.__name__}: {e}")

        logger.info("Graceful shutdown complete")


# Global shutdown manager instance
_shutdown_manager: GracefulShutdown | None = None


def get_shutdown_manager() -> GracefulShutdown:
    """Get the global shutdown manager.

    Returns:
        Shutdown manager instance.
    """
    global _shutdown_manager
    if _shutdown_manager is None:
        _shutdown_manager = GracefulShutdown()
    return _shutdown_manager


async def run_with_shutdown(
    main_coro: Coroutine[Any, Any, None],
    cleanup_coros: list[Callable[[], Coroutine[Any, Any, None]]] | None = None,
    timeout: float = 30.0,
) -> None:
    """Run a coroutine with graceful shutdown handling.

    Args:
        main_coro: Main coroutine to run.
        cleanup_coros: Cleanup coroutines to run on shutdown.
        timeout: Shutdown timeout in seconds.
    """
    shutdown = GracefulShutdown(timeout=timeout)
    shutdown.install_signal_handlers()

    if cleanup_coros:
        for coro in cleanup_coros:
            shutdown.register_cleanup(coro)

    main_task = asyncio.create_task(main_coro)
    shutdown.register_task(main_task)

    try:
        # Run until main completes or shutdown triggered
        done, pending = await asyncio.wait(
            [
                main_task,
                asyncio.create_task(shutdown.wait_for_shutdown()),
            ],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # If shutdown was triggered, cancel main
        if not main_task.done():
            main_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await main_task

    finally:
        await shutdown.shutdown()
