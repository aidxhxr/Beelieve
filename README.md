<p align="center">
  <img src="frontend/public/assets/icons/download.png" alt="Beelieve" width="96" />
</p>

<h1 align="center">Beelieve 🐝</h1>

<p align="center"><b>Precision beekeeping platform — real-time hive telemetry, ML-driven swarm prediction, and an LLM beekeeping advisor.</b></p>

<p align="center">
  <img src="https://img.shields.io/badge/Kafka-231F20?logo=apachekafka&logoColor=white" />
  <img src="https://img.shields.io/badge/MQTT-660066?logo=mqtt&logoColor=white" />
  <img src="https://img.shields.io/badge/TimescaleDB-FDB515?logo=timescale&logoColor=black" />
  <img src="https://img.shields.io/badge/LightGBM-9ACD32" />
  <img src="https://img.shields.io/badge/Mistral--7B-FF7000" />
  <img src="https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white" />
  <img src="https://img.shields.io/badge/React-61DAFB?logo=react&logoColor=black" />
  <img src="https://img.shields.io/badge/ESP32-E7352C?logo=espressif&logoColor=white" />
</p>

---

Beelieve turns any beehive into a monitored, ML-scored asset. In-hive ESP32 sensor
nodes stream temperature, humidity, weight, CO₂ and acoustic-spectrum telemetry over
MQTT; a Kafka pipeline enriches it with rolling features and lands it in TimescaleDB;
a LightGBM ensemble scores every hive in real time for **swarm risk, colony health,
and acoustic anomalies** (queen loss detection from the hive's sound signature); and a
**fine-tuned Mistral-7B advisor** turns those signals into concrete, prioritized
recommendations in English, Russian, or Kazakh.

- 🚀 Launched to **300+ beekeepers**, reaching **$1,000 MRR**
- 💰 **$30,000+ in funding** — Samsung Innovations ([featured story](https://innovation.samsung.com/)), UNESCO Startups, and the KZ Ministry of Ecology
- 📡 Real-time ML pipeline over **500,000+ sensor datapoints** (Kafka · LightGBM · MQTT · TimescaleDB) plus a fine-tuned **Mistral-7B** recommender

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

Full contracts (MQTT/Kafka payloads, feature definitions, DB schema) in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## What's in the box

| Component | Path | Stack |
|---|---|---|
| Hive sensor firmware | [`firmware/`](firmware) | ESP32 (PlatformIO), SHT31, HX711, INMP441 I²S mic w/ Goertzel band energies, MH-Z19, LittleFS offline buffering |
| MQTT → Kafka bridge | [`services/ingestion`](services/ingestion) | paho-mqtt, confluent-kafka, pydantic validation, DLQ |
| Stream processor | [`services/stream-processor`](services/stream-processor) | Kafka consumer, rolling-window feature engineering, batched TimescaleDB writes, rule alerts |
| ML training + inference | [`services/ml`](services/ml) | LightGBM (swarm classifier, health regressor, anomaly multiclass), <10 ms/reading online scoring |
| LLM advisor | [`services/recommender`](services/recommender) | Mistral-7B-Instruct + QLoRA fine-tune (PEFT/TRL), HF Inference serving, en/ru/kk |
| Backend API | [`services/api`](services/api) | FastAPI, JWT auth, async psycopg, live WebSocket fan-out from Kafka |
| Dashboard | [`frontend/`](frontend) | React 18, MUI, ApexCharts, live WS updates |
| Storage | [`db/`](db) | TimescaleDB hypertables, continuous aggregates, compression + retention policies |
| Hive simulator | [`services/simulator`](services/simulator) | Stochastic colony model incl. pre-swarm & queenless acoustic signatures |

## Quickstart

```bash
cp .env.example .env          # fill in secrets (HF_API_KEY etc.)
docker compose up -d --build  # full stack: Mosquitto, Kafka, TimescaleDB, all services
docker compose --profile dev up -d hive-simulator   # synthetic hives for local dev
```

Dashboard: http://localhost:3030 (demo login `demo@beelieve.kz` / `demo1234`) ·
API docs: http://localhost:8000/docs · Recommender: http://localhost:8100/docs

### Train the models

```bash
cd services/ml
python -m training.datagen            # bootstrap synthetic labeled seasons
python -m training.train              # trains swarm / health / anomaly boosters → models/
```

### Fine-tune the advisor

```bash
cd services/recommender/finetune
python dataset.py                     # build instruction dataset (en/ru/kk)
python train_lora.py --push           # QLoRA on Mistral-7B-Instruct, pushes adapter to HF
```

## Engineering highlights

- **Exactly-once-ish ingestion**: QoS 1 MQTT + idempotent keyed Kafka producer + at-least-once DB writes with manual offset commits after flush.
- **Feature parity between training and serving**: one `FEATURE_COLUMNS` contract shared by the offline trainer and the online scorer — no train/serve skew.
- **Acoustic swarm detection**: 5-band Goertzel energies (100–600 Hz) computed on-device; pre-swarm hives shift energy into 400–600 Hz — the model picks this up ~48–72 h before swarming.
- **TimescaleDB done properly**: hypertables with compression after 7 days, 12-month retention, real-time continuous aggregates powering the dashboard's hourly/daily charts.
- **LLM with a safety net**: the advisor's structured-output parser tolerates format drift, and a deterministic rule-based fallback answers when the HF endpoint is unavailable.
- **Offline-first firmware**: readings buffered to LittleFS when the uplink is down, replayed on reconnect; deep-sleep duty cycle for months of battery life.

## License

MIT — see [LICENSE](LICENSE).
