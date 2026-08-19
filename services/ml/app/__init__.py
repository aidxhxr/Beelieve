"""Online inference half of the Beelieve ML service.

Kafka consumer (group ``ml-inference``) on ``hive.telemetry.enriched`` that
scores every reading with three LightGBM heads and produces to
``hive.predictions`` (plus critical alerts to ``hive.alerts``).
"""
