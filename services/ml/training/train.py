"""Train the three Beelieve LightGBM heads from a labeled parquet dataset.

Heads (see docs/ARCHITECTURE.md, "ML"):
    * swarm   -- binary classifier (swarm_within_72h), AUC early stopping
    * health  -- regressor (health_score), l2 objective
    * anomaly -- multiclass classifier (anomaly_kind over ANOMALY_KINDS)

Splitting is grouped by hive_id so no hive leaks between train and valid.
Class imbalance is handled with per-sample inverse-frequency weights.

Artifacts land in services/ml/models/ (see models/README.md):
    swarm-{MODEL_VERSION}.txt, health-{MODEL_VERSION}.txt,
    anomaly-{MODEL_VERSION}.txt, metrics-{MODEL_VERSION}.json,
    feature_importance-{MODEL_VERSION}.json

Usage:
    python -m training.train --data data/generated/dataset.parquet
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    r2_score,
    roc_auc_score,
)
from sklearn.model_selection import GroupShuffleSplit

from training.features import ANOMALY_KINDS, FEATURE_COLUMNS

logger = logging.getLogger("training.train")

ML_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA = ML_ROOT / "data" / "generated" / "dataset.parquet"
DEFAULT_OUT = ML_ROOT / "models"

COMMON_PARAMS: dict[str, Any] = {
    "boosting_type": "gbdt",
    "learning_rate": 0.05,
    "num_leaves": 63,
    "min_data_in_leaf": 40,
    "feature_fraction": 0.9,
    "bagging_fraction": 0.9,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "verbosity": -1,
    "use_missing": True,
}


def _split(df: pd.DataFrame, valid_frac: float, seed: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Group-aware train/valid split so no hive appears in both sides."""
    splitter = GroupShuffleSplit(n_splits=1, test_size=valid_frac, random_state=seed)
    train_idx, valid_idx = next(splitter.split(df, groups=df["hive_id"]))
    return df.iloc[train_idx], df.iloc[valid_idx]


def _class_weights(labels: np.ndarray) -> np.ndarray:
    """Per-sample inverse-frequency weights, normalized to mean 1."""
    classes, counts = np.unique(labels, return_counts=True)
    weight_by_class = {c: len(labels) / (len(classes) * n) for c, n in zip(classes, counts)}
    weights = np.array([weight_by_class[label] for label in labels], dtype=np.float64)
    return weights / weights.mean()


def _fit(
    params: dict[str, Any],
    x_train: pd.DataFrame,
    y_train: np.ndarray,
    x_valid: pd.DataFrame,
    y_valid: np.ndarray,
    train_weights: np.ndarray | None,
    num_boost_round: int,
    early_stopping_rounds: int,
) -> lgb.Booster:
    dtrain = lgb.Dataset(x_train, label=y_train, weight=train_weights, feature_name=FEATURE_COLUMNS)
    dvalid = lgb.Dataset(x_valid, label=y_valid, reference=dtrain)
    return lgb.train(
        {**COMMON_PARAMS, **params},
        dtrain,
        num_boost_round=num_boost_round,
        valid_sets=[dvalid],
        valid_names=["valid"],
        callbacks=[
            lgb.early_stopping(stopping_rounds=early_stopping_rounds, verbose=True),
            lgb.log_evaluation(period=100),
        ],
    )


def train_swarm(train_df: pd.DataFrame, valid_df: pd.DataFrame, rounds: int, patience: int) -> tuple[lgb.Booster, dict[str, float]]:
    y_train = train_df["swarm_within_72h"].to_numpy(dtype=np.int32)
    y_valid = valid_df["swarm_within_72h"].to_numpy(dtype=np.int32)
    booster = _fit(
        {"objective": "binary", "metric": "auc", "first_metric_only": True},
        train_df[FEATURE_COLUMNS], y_train,
        valid_df[FEATURE_COLUMNS], y_valid,
        _class_weights(y_train), rounds, patience,
    )
    proba = np.asarray(booster.predict(valid_df[FEATURE_COLUMNS], num_iteration=booster.best_iteration))
    metrics = {
        "auc": float(roc_auc_score(y_valid, proba)),
        "positive_rate_valid": float(y_valid.mean()),
        "best_iteration": int(booster.best_iteration or 0),
    }
    logger.info("swarm head: %s", metrics)
    return booster, metrics


def train_health(train_df: pd.DataFrame, valid_df: pd.DataFrame, rounds: int, patience: int) -> tuple[lgb.Booster, dict[str, float]]:
    y_train = train_df["health_score"].to_numpy(dtype=np.float64)
    y_valid = valid_df["health_score"].to_numpy(dtype=np.float64)
    booster = _fit(
        {"objective": "regression", "metric": "l2"},
        train_df[FEATURE_COLUMNS], y_train,
        valid_df[FEATURE_COLUMNS], y_valid,
        None, rounds, patience,
    )
    pred = np.clip(np.asarray(booster.predict(valid_df[FEATURE_COLUMNS], num_iteration=booster.best_iteration)), 0.0, 1.0)
    metrics = {
        "rmse": float(np.sqrt(mean_squared_error(y_valid, pred))),
        "mae": float(mean_absolute_error(y_valid, pred)),
        "r2": float(r2_score(y_valid, pred)),
        "best_iteration": int(booster.best_iteration or 0),
    }
    logger.info("health head: %s", metrics)
    return booster, metrics


def train_anomaly(train_df: pd.DataFrame, valid_df: pd.DataFrame, rounds: int, patience: int) -> tuple[lgb.Booster, dict[str, Any]]:
    kind_to_id = {kind: i for i, kind in enumerate(ANOMALY_KINDS)}
    y_train = train_df["anomaly_kind"].astype(str).map(kind_to_id).to_numpy(dtype=np.int32)
    y_valid = valid_df["anomaly_kind"].astype(str).map(kind_to_id).to_numpy(dtype=np.int32)
    booster = _fit(
        {"objective": "multiclass", "num_class": len(ANOMALY_KINDS), "metric": "multi_logloss"},
        train_df[FEATURE_COLUMNS], y_train,
        valid_df[FEATURE_COLUMNS], y_valid,
        _class_weights(y_train), rounds, patience,
    )
    proba = np.asarray(booster.predict(valid_df[FEATURE_COLUMNS], num_iteration=booster.best_iteration))
    pred = proba.argmax(axis=1)
    per_class_f1 = f1_score(y_valid, pred, average=None, labels=list(range(len(ANOMALY_KINDS))), zero_division=0)
    metrics: dict[str, Any] = {
        "accuracy": float(accuracy_score(y_valid, pred)),
        "macro_f1": float(f1_score(y_valid, pred, average="macro", zero_division=0)),
        "f1_per_class": {kind: float(score) for kind, score in zip(ANOMALY_KINDS, per_class_f1)},
        "best_iteration": int(booster.best_iteration or 0),
    }
    logger.info("anomaly head: %s", metrics)
    return booster, metrics


def _importance(booster: lgb.Booster) -> dict[str, float]:
    gains = booster.feature_importance(importance_type="gain")
    return {name: float(gain) for name, gain in sorted(zip(FEATURE_COLUMNS, gains), key=lambda kv: -kv[1])}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA, help="labeled parquet dataset")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT, help="artifact output directory")
    parser.add_argument("--model-version", default=os.environ.get("MODEL_VERSION", "lgbm-2026.08"))
    parser.add_argument("--valid-frac", type=float, default=0.2, help="validation fraction (grouped by hive_id)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--num-boost-round", type=int, default=2000)
    parser.add_argument("--early-stopping-rounds", type=int, default=100)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    if not args.data.exists():
        raise SystemExit(
            f"dataset not found: {args.data} -- run `python -m training.datagen` "
            "or `python -m training.export_from_timescale` first"
        )
    df = pd.read_parquet(args.data)
    missing = [c for c in (*FEATURE_COLUMNS, "hive_id", "swarm_within_72h", "health_score", "anomaly_kind") if c not in df.columns]
    if missing:
        raise SystemExit(f"dataset {args.data} is missing required columns: {missing}")

    train_df, valid_df = _split(df, args.valid_frac, args.seed)
    logger.info(
        "train: %d rows / %d hives | valid: %d rows / %d hives",
        len(train_df), train_df["hive_id"].nunique(), len(valid_df), valid_df["hive_id"].nunique(),
    )

    rounds, patience = args.num_boost_round, args.early_stopping_rounds
    swarm_booster, swarm_metrics = train_swarm(train_df, valid_df, rounds, patience)
    health_booster, health_metrics = train_health(train_df, valid_df, rounds, patience)
    anomaly_booster, anomaly_metrics = train_anomaly(train_df, valid_df, rounds, patience)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    version = args.model_version
    for name, booster in (("swarm", swarm_booster), ("health", health_booster), ("anomaly", anomaly_booster)):
        path = args.out_dir / f"{name}-{version}.txt"
        booster.save_model(str(path), num_iteration=booster.best_iteration)
        logger.info("saved %s", path)

    metrics = {
        "model_version": version,
        "dataset": str(args.data),
        "n_train": int(len(train_df)),
        "n_valid": int(len(valid_df)),
        "seed": args.seed,
        "feature_columns": FEATURE_COLUMNS,
        "anomaly_kinds": ANOMALY_KINDS,
        "swarm": swarm_metrics,
        "health": health_metrics,
        "anomaly": anomaly_metrics,
    }
    (args.out_dir / f"metrics-{version}.json").write_text(json.dumps(metrics, indent=2) + "\n")

    importance = {
        "swarm": _importance(swarm_booster),
        "health": _importance(health_booster),
        "anomaly": _importance(anomaly_booster),
    }
    (args.out_dir / f"feature_importance-{version}.json").write_text(json.dumps(importance, indent=2) + "\n")
    logger.info("wrote metrics and feature importance for %s to %s", version, args.out_dir)


if __name__ == "__main__":
    main()
