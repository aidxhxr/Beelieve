"""Offline training half of the Beelieve ML service.

Modules:
    features              -- THE feature contract shared with online inference.
    datagen               -- synthetic labeled dataset generator (bootstrapping).
    train                 -- LightGBM training CLI (swarm / health / anomaly).
    export_from_timescale -- export real labeled data from TimescaleDB.
"""
