# Beelieve Recommender — fine-tuned Mistral-7B beekeeping advisor

Turns a hive's telemetry snapshot, ML predictions and recent alerts into 1-3
ranked, actionable recommendations in the beekeeper's language (en/ru/kk).

Two halves:

- `finetune/` — build the instruction dataset, QLoRA-tune
  `mistralai/Mistral-7B-Instruct-v0.3`, merge + evaluate, push the adapter to
  the Hub (`HF_MODEL_ID`).
- `app/` — FastAPI service that gathers context from TimescaleDB, calls the
  fine-tuned model via the Hugging Face Inference API, parses and stores the
  output, and falls back to deterministic rules whenever HF is unavailable.

## Configuration (env only, see repo `.env.example`)

| Var | Purpose | Default |
|---|---|---|
| `DATABASE_URL` | TimescaleDB DSN | `postgresql://beelieve:...@timescaledb:5432/beelieve` |
| `HF_API_KEY` | Hugging Face token (never hard-code; the literal placeholder `HF_API_KEY` counts as unset) | — |
| `HF_MODEL_ID` | Fine-tuned adapter repo served via Inference API | `aidxhxr/beelieve-mistral-7b-advisor` |
| `HF_BASE_MODEL` | Base model for fine-tuning | `mistralai/Mistral-7B-Instruct-v0.3` |
| `RECOMMENDER_PORT` | Service port | `8100` |
| `RECOMMENDER_LANG_DEFAULT` | Locale when the request omits one | `en` |

When `HF_API_KEY` is unset/placeholder, or the HF call fails (after a timeout
and one retry), the service answers from `app/fallback.py` and marks the rows
with `model_id = "fallback-rules"` — the endpoint always answers.

## Fine-tune workflow

Run from `services/recommender` on a machine with a single 24 GB GPU:

```bash
pip install -r finetune/requirements.txt

# 1. Build ~1k chat-format examples (JSONL: system/user/assistant) from ~40
#    curated seeds (swarm, queenless, varroa timing, feeding, continental
#    wintering, ventilation, honey flow; en/ru/kk) with programmatic
#    number/hive/season augmentation. Uses the exact runtime prompt.
python -m finetune.dataset --n 1000 --seed 42

# 2. QLoRA fine-tune: 4-bit NF4 base, LoRA r=16 alpha=32 on
#    q/k/v/o/gate/up/down projections, packing, cosine schedule.
export HF_API_KEY=...           # your real token, from the environment
python -m finetune.train_lora --push   # --push uploads the adapter to $HF_MODEL_ID

# 3. Merge the adapter and evaluate on the held-out split:
#    exact-format compliance (via the production parser) + ROUGE-L F1.
python -m finetune.merge_and_eval --limit 50
# -> finetune/out/eval_report.json
```

## Serving

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port "${RECOMMENDER_PORT:-8100}"
# or: docker build -t beelieve-recommender . && docker run --env-file ../../.env -p 8100:8100 beelieve-recommender
```

### API

- `POST /recommendations` — body `{"hive_id": "KZ-ALA-0042", "locale": "ru"}`
  (locale optional: `en|ru|kk`). Gathers the latest `sensor_readings` and
  `predictions` rows, alerts from the last 72 h and hive/apiary metadata,
  prompts the model (temperature 0.3, max 512 tokens), parses 1-3
  `{priority 1-5, title, body}` recommendations, stores them in
  `recommendations` (context snapshot in the JSONB column) and returns them
  with the `model_id` used.
- `GET /recommendations/{hive_id}?limit=10` — recent stored recommendations.
- `GET /healthz` — liveness, HF/DB status.

### Output contract

The model must reply in this exact format (markers always English uppercase,
text in the requested locale); `app/parse.py` tolerates minor drift:

```
RECOMMENDATION 1
PRIORITY: 1
TITLE: Inspect for queen cells and split if capped
BODY: Swarm risk is 82% ...
```

## Tests

Pure-logic tests (no DB, no network, no ML deps):

```bash
pip install pytest && python -m pytest tests/ -q
```
