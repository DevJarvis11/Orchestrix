"""The worker's Taskiq broker: the shared one from orchestrix_core.queue (Redis Streams)."""

from orchestrix_core.queue import get_broker

broker = get_broker()
