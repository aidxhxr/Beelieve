FROM python:3.14-slim

# libgomp1: OpenMP runtime required by LightGBM wheels.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    MODEL_DIR=/models

WORKDIR /srv/ml

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY training/ ./training/
COPY app/ ./app/

# Non-root runtime user; model artifacts are mounted read-only at /models.
RUN useradd --create-home --uid 10001 beelieve \
    && mkdir -p /models \
    && chown -R beelieve:beelieve /srv/ml /models
USER beelieve

CMD ["python", "-m", "app.main"]
