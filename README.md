<p align="center">
  <img src="frontend/public/assets/icons/download.png" alt="Beelieve" width="96" />
</p>

# Beelieve

Precision beekeeping platform: real-time hive telemetry, ML swarm prediction, and
an LLM advisor for beekeepers.

ESP32 nodes inside the hives measure temperature, humidity, weight, CO2 and the
sound spectrum, and stream it all over MQTT. A Kafka pipeline enriches the data
with rolling features and lands it in TimescaleDB. A LightGBM ensemble scores
every hive continuously for swarm risk, colony health and acoustic anomalies
(you can hear queen loss in a hive's sound signature), and a fine-tuned
Mistral-7B turns those signals into concrete recommendations in English, Russian
or Kazakh.

The project is live: 300+ beekeepers use it, it makes about $1,000 MRR, and it
has raised over $30,000 in grants and prizes (Samsung Innovations, UNESCO
Startups, the KZ Ministry of Ecology). The pipeline has processed 500,000+
sensor datapoints so far.

A note on the history: this project was completed a while ago but lived only on
my machine — I just never uploaded it. This repo is me finally putting it on
GitHub, so the commit history starts much later than the actual work did.

## Architecture

```
ESP32 sensor node ──MQTT──▶ Mosquitto ──▶ ingestion bridge ──▶ Kafka (hive.telemetry.raw)
                                                                  │
                        ┌─────────────────────────────────────────┼───────────────┐
                        ▼                                         ▼               │
               stream-processor                             ml-inference          │
          rolling features + Timescale writer            LightGBM × 3 heads       │
                        │            enriched ─────────▶  swarm / health /        │
                        ▼                                  anomaly scoring        │
                  TimescaleDB                                     │               │
            hypertables + continuous                       hive.predictions       │
            aggregates + compression                       hive.alerts ◀──────────┘
                        │
                        ▼
              FastAPI + WebSocket ◀────▶ Mistral-7B advisor (LoRA fine-tune, HF serving)
                        │
                        ▼
                React dashboard (live updates over WS)
```

The exact contracts (MQTT/Kafka payloads, feature definitions, DB schema) are in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Layout

| Path | What it is |
|---|---|
| [`firmware/`](firmware) | ESP32 firmware (PlatformIO): SHT31, HX711, I2S mic with Goertzel band energies, MH-Z19, LittleFS buffering for offline periods |
| [`services/ingestion`](services/ingestion) | MQTT → Kafka bridge with pydantic validation and a DLQ |
| [`services/stream-processor`](services/stream-processor) | Rolling-window features, batched TimescaleDB writes, rule alerts |
| [`services/ml`](services/ml) | LightGBM training + online scoring (swarm, health, anomaly) |
| [`services/recommender`](services/recommender) | Mistral-7B advisor: QLoRA fine-tune, HF serving, rule-based fallback |
| [`services/api`](services/api) | FastAPI backend: JWT auth, async psycopg, WebSocket fan-out from Kafka |
| [`services/simulator`](services/simulator) | Dev-only stochastic hive simulator (incl. pre-swarm and queenless acoustics) |
| [`frontend/`](frontend) | React dashboard with live WS updates |
| [`db/`](db) | TimescaleDB schema: hypertables, continuous aggregates, compression/retention |

## Quickstart

```bash
cp .env.example .env          # fill in secrets (HF_API_KEY etc.)
docker compose up -d --build
docker compose --profile dev up -d hive-simulator   # synthetic hives for local dev
```

Dashboard at http://localhost:3030 (demo login `demo@beelieve.kz` / `demo1234`),
API docs at http://localhost:8000/docs, recommender at http://localhost:8100/docs.

To train the models:

```bash
cd services/ml
python -m training.datagen    # bootstrap synthetic labeled seasons
python -m training.train      # swarm / health / anomaly boosters -> models/
```

To fine-tune the advisor:

```bash
cd services/recommender/finetune
python dataset.py             # build the en/ru/kk instruction dataset
python train_lora.py --push   # QLoRA on Mistral-7B-Instruct, pushes adapter to HF
```

## Design notes

Ingestion is QoS 1 MQTT into an idempotent keyed Kafka producer; the stream
processor commits offsets only after its DB batch lands, so nothing is lost on
crashes (at-least-once, deduped downstream by key and timestamp). Training and
serving share a single `FEATURE_COLUMNS` contract, so there is no train/serve
skew. The acoustic features are five Goertzel band energies (100–600 Hz)
computed on-device; pre-swarm colonies shift energy into the 400–600 Hz bands,
which the model picks up roughly two to three days before swarming. Raw
readings are compressed after 7 days and kept for 12 months, with real-time
continuous aggregates feeding the dashboard charts. The LLM advisor parses
structured output defensively and falls back to deterministic rules when the HF
endpoint is down. Firmware buffers readings to LittleFS when the uplink drops
and replays them on reconnect.

## License

MIT — see [LICENSE](LICENSE).
