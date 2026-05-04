"""Queue consumer for processing webhook events from Redis Streams."""

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable, Coroutine
from typing import Any

import redis.asyncio as redis

from forge.integrations.jira import JiraClient
from forge.models.events import EventSource
from forge.orchestrator.checkpointer import get_redis_client
from forge.queue.models import QueueMessage
from forge.queue.producer import GITHUB_STREAM, JIRA_STREAM

logger = logging.getLogger(__name__)

# Consumer group name
CONSUMER_GROUP = "forge-workers"

# Handler type for message processing
MessageHandler = Callable[[QueueMessage], Coroutine[Any, Any, None]]


class QueueConsumer:
    """Consumes webhook events from Redis Streams with FIFO ordering per ticket.

    Implements consumer groups for distributed processing and ensures
    events for the same ticket are processed sequentially.
    """

    def __init__(
        self,
        consumer_name: str,
        redis_client: redis.Redis | None = None,
        jira_client: JiraClient | None = None,
    ):
        """Initialize the queue consumer.

        Args:
            consumer_name: Unique name for this consumer instance.
            redis_client: Optional Redis client. Creates new if not provided.
            jira_client: Optional Jira client for freshness checks.
        """
        self.consumer_name = consumer_name
        self._redis = redis_client
        self._jira = jira_client
        self._handlers: dict[EventSource, MessageHandler] = {}
        self._running = False
        self._ticket_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def _get_redis(self) -> redis.Redis:
        """Get or create Redis client."""
        if self._redis is None:
            self._redis = await get_redis_client()
        return self._redis

    async def _ensure_consumer_groups(self) -> None:
        """Ensure consumer groups exist for all streams."""
        redis_client = await self._get_redis()

        for stream in [JIRA_STREAM, GITHUB_STREAM]:
            try:
                await redis_client.xgroup_create(stream, CONSUMER_GROUP, id="0", mkstream=True)
                logger.info(f"Created consumer group {CONSUMER_GROUP} for {stream}")
            except redis.ResponseError as e:
                if "BUSYGROUP" not in str(e):
                    raise
                # Group already exists

    def register_handler(self, source: EventSource, handler: MessageHandler) -> None:
        """Register a handler for events from a specific source.

        Args:
            source: Event source to handle.
            handler: Async function to process messages.
        """
        self._handlers[source] = handler
        logger.info(f"Registered handler for {source.value} events")

    async def _check_freshness(self, message: QueueMessage) -> bool:
        """Check if the event is still fresh (ticket state hasn't changed).

        Args:
            message: The message to check.

        Returns:
            True if the event should be processed, False if stale.
        """
        if self._jira is None or message.source != EventSource.JIRA:
            return True

        try:
            issue = await self._jira.get_issue(message.ticket_key)
            event_status = (
                message.payload.get("issue", {}).get("fields", {}).get("status", {}).get("name", "")
            )

            if issue.status != event_status:
                logger.info(
                    f"Stale event for {message.ticket_key}: "
                    f"event status {event_status}, current status {issue.status}"
                )
                return False
            return True
        except Exception as e:
            logger.warning(f"Freshness check failed for {message.ticket_key}: {e}")
            return True  # Process anyway if check fails

    async def _process_message(self, message: QueueMessage) -> None:
        """Process a single message with FIFO ordering per ticket.

        Args:
            message: The message to process.
        """
        handler = self._handlers.get(message.source)
        if handler is None:
            logger.warning(f"No handler for {message.source.value} events")
            return

        # Acquire lock for this ticket to ensure FIFO ordering
        async with self._ticket_locks[message.ticket_key]:
            # Check freshness before processing
            if not await self._check_freshness(message):
                logger.info(f"Skipping stale event {message.event_id}")
                return

            try:
                await handler(message)
                logger.info(f"Processed event {message.event_id}")
            except Exception as e:
                logger.error(f"Error processing {message.event_id}: {e}")
                raise

    async def _consume_stream(self, stream: str, _source: EventSource) -> None:
        """Consume messages from a single stream.

        Args:
            stream: Redis stream name.
            _source: Event source for the stream (unused, for API compatibility).
        """
        redis_client = await self._get_redis()

        while self._running:
            try:
                # Read from consumer group
                messages = await redis_client.xreadgroup(
                    CONSUMER_GROUP,
                    self.consumer_name,
                    {stream: ">"},
                    count=10,
                    block=5000,  # 5 second timeout
                )

                for _stream_name, entries in messages:
                    for message_id, data in entries:
                        message = QueueMessage.from_redis(message_id, data)

                        try:
                            await self._process_message(message)
                            # Acknowledge successful processing
                            await redis_client.xack(stream, CONSUMER_GROUP, message_id)
                        except Exception:
                            # Message will be retried (not acknowledged)
                            pass

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error consuming from {stream}: {e}")
                await asyncio.sleep(1)  # Brief pause before retry

    async def start(self) -> None:
        """Start consuming from all registered streams."""
        await self._ensure_consumer_groups()
        self._running = True

        tasks = []
        if EventSource.JIRA in self._handlers:
            tasks.append(self._consume_stream(JIRA_STREAM, EventSource.JIRA))
        if EventSource.GITHUB in self._handlers:
            tasks.append(self._consume_stream(GITHUB_STREAM, EventSource.GITHUB))

        if tasks:
            logger.info(f"Consumer {self.consumer_name} starting...")
            await asyncio.gather(*tasks)

    async def stop(self) -> None:
        """Stop consuming messages."""
        self._running = False
        logger.info(f"Consumer {self.consumer_name} stopped")
