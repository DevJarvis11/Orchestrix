# apps/worker

Taskiq worker and scheduler (SPEC 8): runs graph steps under a Postgres run lease (heartbeat, reaper, crash-loop guard), publishes events through the outbox, mirrors interrupts into escalations, and runs the periodic jobs (SLA, memory backfill and consolidation, retention, gauges).
