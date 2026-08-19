# Model artifacts

Trained LightGBM boosters land in this directory (or wherever `--out-dir`
points; in containers they are mounted at `MODEL_DIR`, default `/models`).

## Naming convention

One plain-text LightGBM Booster file per head, suffixed with the model
version (`MODEL_VERSION` env var, e.g. `lgbm-2026.08`):

| File | Head | Objective |
|---|---|---|
| `swarm-{MODEL_VERSION}.txt` | swarm-risk classifier | binary (swarm_within_72h), AUC early stopping |
| `health-{MODEL_VERSION}.txt` | health-score regressor | regression (l2), target in [0, 1] |
| `anomaly-{MODEL_VERSION}.txt` | anomaly classifier | multiclass over `training.features.ANOMALY_KINDS` |

Alongside the boosters, `training.train` writes:

- `metrics-{MODEL_VERSION}.json` — validation metrics (AUC, RMSE/MAE/R2,
  accuracy, macro-F1 and per-class F1), split sizes, seed, and the exact
  feature-column and anomaly-kind lists the artifacts were trained with.
- `feature_importance-{MODEL_VERSION}.json` — gain importances per head.

## Rules

- Artifacts are **not** committed (see `../.gitignore`); only this README and
  `.gitkeep` live in git.
- The online scorer (`app.scorer.MLScorer.load`) resolves artifacts as
  `{MODEL_DIR}/{head}-{MODEL_VERSION}.txt` and fails fast if any is missing
  (unless `ML_ALLOW_HEURISTIC=true`).
- Never reuse a `MODEL_VERSION` for retrained artifacts, and bump it whenever
  `training.features.FEATURE_COLUMNS` or `ANOMALY_KINDS` change — boosters
  are positional over that contract.
