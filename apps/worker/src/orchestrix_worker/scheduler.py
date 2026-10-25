"""Periodic jobs: lease reaper, SLA checks, consolidation, retention (schedules added from M2).

Run with `taskiq scheduler orchestrix_worker.scheduler:scheduler`.
"""

from taskiq import TaskiqScheduler
from taskiq.schedule_sources import LabelScheduleSource

from orchestrix_worker.broker import broker

scheduler = TaskiqScheduler(broker=broker, sources=[LabelScheduleSource(broker)])
