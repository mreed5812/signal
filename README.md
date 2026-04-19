# BTC Pipeline

A production-quality pipeline that collects crypto, forex, on-chain, and news data every 15 minutes, trains an XGBoost model to predict the next-day Bitcoin price, and serves predictions via a FastAPI backend with a live dashboard.

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│  Docker Compose (shared network: btc_net)                            │
│                                                                      │
│  ┌──────────┐  ┌──────────┐  ┌───────────┐  ┌────────────────────┐  │
│  │ postgres │  │scheduler │  │    api     │  │prometheus + grafana│  │
│  │  :5432   │  │  (cron)  │  │   :8000   │  │    :9090 / :3000   │  │
│  └────┬─────┘  └────┬─────┘  └─────┬─────┘  └────────────────────┘  │
│       │              │              │                                  │
│  Named volumes:  postgres_data  model_artifacts  hf_cache  grafana   │
└──────────────────────────────────────────────────────────────────────┘

Scheduler jobs (all via cron, staggered):
  Every 15 min → CoinGecko · Yahoo Finance · NewsAPI · On-chain
  Every 15 min (offset 8 min) → Sentiment worker (VADER + FinBERT)
  Daily 00:30 UTC → Feature builder
  Daily 01:00 UTC → XGBoost trainer
  Daily 01:30 UTC → Predictor
```

## Quickstart

```bash
git clone <repo-url> btc-pipeline && cd btc-pipeline
cp .env.example .env
# Edit .env — at minimum set NEWS_API_KEY (free at https://newsapi.org/register)
docker compose up --build
```

- Dashboard: http://localhost:8000
- Grafana: http://localhost:3000 (admin / admin)
- Prometheus: http://localhost:9090
- API docs: http://localhost:8000/docs

## Pi 5 Deployment (ARM64)

```bash
# On your dev machine — build multi-arch image and push to a registry
docker buildx create --use
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t your-registry/btc-pipeline:latest \
  --push .

# On the Pi 5
docker compose pull
docker compose up -d
```

All base images (`postgres:16-alpine`, `prom/prometheus`, `grafana/grafana`, `python:3.12-slim`) publish ARM64 builds on Docker Hub. PyTorch is installed from `https://download.pytorch.org/whl/cpu` to avoid CUDA wheels.

## API Key Acquisition

| Service | Link | Notes |
|---------|------|-------|
| NewsAPI | https://newsapi.org/register | Free tier: 100 req/day |
| CoinGecko | *(no key needed)* | Optional Pro key in `.env` |
| Yahoo Finance | *(no key needed)* | Via `yfinance` |
| Blockchain.com | *(no key needed)* | Public charts API |

## Free-Tier Limits

| Source | Rate Limit | History Depth | On Exhaustion |
|--------|-----------|---------------|---------------|
| CoinGecko | 30 req/min | Full history | HTTP 429 → exponential backoff |
| Yahoo Finance | ~2000 req/day (informal) | Full history | Exception → tenacity retry |
| NewsAPI | 100 req/day | 30 days | Log warning, stop fetching |
| Blockchain.com | No documented limit | Full history | Exception → tenacity retry |

**NewsAPI limitation**: the free tier only allows fetching headlines up to 30 days old. Sentiment features for dates before that window use a neutral prior (0.0). This is documented in the feature builder.

## Adding a New Data Source

1. Create `src/sources/mysource.py`.
2. Subclass `DataSource` from `src.sources.base`:

```python
class MySource(DataSource):
    name = "mysource"

    def fetch_historical(self, start, end): ...
    def fetch_latest(self): ...
    def store(self, df): ...
```

3. Add a runner: `src/sources/mysource_runner.py` calling `MySource().run()`.
4. Add a cron line to `src/scheduler/crontab`.
5. Add an Alembic migration if you need a new table.

## Grafana Dashboards

The dashboard at http://localhost:3000 (auto-provisioned) shows:

- **Job Success Rate**: fraction of pipeline runs in the last hour that succeeded.
- **Model RMSE / Prediction Error**: live model quality metrics.
- **Rows Ingested / 15m**: throughput per source.
- **Job Duration p95**: slowest jobs to watch.
- **API Request Rate**: per-handler request counts.
- **Data Freshness**: seconds since last successful ingest per source — alert fires at 30 min.

## Alerting

Alerts are defined in `prometheus/alert_rules.yml`. To route to Discord/Telegram/email, configure an Alertmanager instance and point the `alertmanagers` block in `prometheus/prometheus.yml` at it. Each alert annotation includes a `summary` suitable for a notification message.

## Known Limitations

- NewsAPI free tier limits historical news to 30 days; early periods use a neutral sentiment prior.
- CoinGecko's free-tier market_chart endpoint returns only close prices (not true OHLCV). Pro key unlocks real OHLCV via `/coins/{id}/ohlc`.
- FinBERT on CPU (Pi 5) takes ~1–2 seconds per headline; the sentiment worker batches 32 at a time to amortize this.
- XGBoost confidence intervals use a Gaussian assumption over validation RMSE — they're directionally correct but not calibrated.

## Future Work

- Add Reddit/r/CryptoCurrency and X (Twitter) sentiment via their APIs.
- Experiment with LSTMs or temporal fusion transformers for the model.
- Add a dedicated Alertmanager container with pre-wired Discord webhook.
- Optionally upgrade to CoinGecko Pro for true OHLCV data.
