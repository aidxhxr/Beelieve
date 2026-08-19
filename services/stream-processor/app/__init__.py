"""Beelieve stream processor.

Consumes raw hive telemetry from Kafka, computes rolling features, persists
readings/predictions/alerts to TimescaleDB, and emits rule-based alerts.
"""

__all__ = ["__version__"]

__version__ = "1.0.0"
