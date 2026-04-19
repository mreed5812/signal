# BTC Pipeline — Architecture Reference

This document is the single source of truth for how the system is structured, how data moves through it, and how services relate to one another. Every diagram uses service names that match `docker-compose.yml` exactly. When you change the system, update the corresponding diagram in the same PR — a one-line note at the bottom tells you which diagram owns which concern.

---

## Table of Contents

1. [System Context](#1-system-context)
2. [Container Diagram](#2-container-diagram)
3. [Data Flow](#3-data-flow)
4. [Database Schema](#4-database-schema)
5. [Scheduling Timeline](#5-scheduling-timeline)
6. [Model Training and Prediction Sequence](#6-model-training-and-prediction-sequence)
7. [API and Dashboard Request Flow](#7-api-and-dashboard-request-flow)
8. [Observability Flow](#8-observability-flow)
9. [Deployment Topology](#9-deployment-topology)
10. [Updating These Diagrams](#updating-these-diagrams)

---

## 1. System Context

This diagram answers: *"What external systems does the pipeline touch, and who consumes its output?"* The application sits between free-tier external APIs (left) and the end-user dashboard (right). Nothing inside the container boundary requires paid credentials except `NEWS_API_KEY`; everything else is anonymous. The boundary also clarifies what is *not* our problem — rate limiting, availability, and schema changes on the external APIs are outside our control and are handled defensively with retries and graceful degradation.

```mermaid
flowchart LR
    subgraph External["External Data Sources (free tier)"]
        CG["CoinGecko API\n30 req/min, no key"]
        YF["Yahoo Finance\nyfinance library"]
        NA["NewsAPI.org\n100 req/day, 30-day depth"]
        BC["blockchain.com\nPublic charts API"]
    end

    subgraph App["btc-pipeline (Docker Compose)"]
        direction TB
        ING["Ingestion Workers"]
        DB[("PostgreSQL")]
        SENT["Sentiment Worker\nVADER + FinBERT"]
        FEAT["Feature Builder"]
        MDL["XGBoost Model"]
        API["FastAPI / Dashboard"]
    end

    subgraph Consumers["End Users"]
        BROWSER["Browser\nlocalhost:8000"]
        GRAFANA["Grafana\nlocalhost:3000"]
        PROM["Prometheus\nlocalhost:9090"]
    end

    CG -->|"OHLCV (HTTPS)"| ING
    YF -->|"Forex / indices (HTTPS)"| ING
    NA -->|"Headlines (HTTPS)"| ING
    BC -->|"On-chain metrics (HTTPS)"| ING

    ING -->|"raw rows"| DB
    DB -->|"unprocessed news"| SENT
    SENT -->|"sentiment scores"| DB
    DB -->|"daily features"| FEAT
    FEAT -->|"feature table"| DB
    DB -->|"features"| MDL
    MDL -->|"model artifact + prediction"| DB
    DB -->|"queries"| API

    API --> BROWSER
    API --> GRAFANA
    API --> PROM
```

---

## 2. Container Diagram

This diagram answers: *"Which Docker services exist, how do they communicate, and which volumes does each one own?"* Every service shares the `btc_net` Docker network, so they resolve each other by service name. One-shot services (`migrate`, `backfill`) are shown with dashed borders — they exit when done and are never restarted. Named volumes are shown as cylinders; a service that mounts a volume in read-only mode is marked `:ro`.

```mermaid
flowchart TD
    subgraph net["Docker network: btc_net"]
        direction TB

        subgraph oneshot["One-shot services (restart: no)"]
            MIGRATE["migrate\nalembic upgrade head"]
            BACKFILL["backfill\nscripts/backfill.py"]
        end

        subgraph longrunning["Long-running services"]
            POSTGRES[("postgres\nport 5432")]
            SCHEDULER["scheduler\ncron daemon"]
            API["api\nport 8000"]
            PROM["prometheus\nport 9090"]
            GRAF["grafana\nport 3000"]
        end
    end

    subgraph vols["Named volumes"]
        V_PG[("postgres_data")]
        V_MOD[("model_artifacts")]
        V_HF[("hf_cache\nHugging Face weights")]
        V_PROM[("prometheus_data")]
        V_GRAF[("grafana_data")]
    end

    %% service-to-service communication
    MIGRATE -->|"SQL (waits for healthy)"| POSTGRES
    BACKFILL -->|"SQL (after migrate)"| POSTGRES
    SCHEDULER -->|"SQL"| POSTGRES
    API -->|"SQL"| POSTGRES
    PROM -->|"HTTP scrape /metrics"| API
    GRAF -->|"HTTP PromQL"| PROM

    %% volume mounts
    POSTGRES --- V_PG
    SCHEDULER --- V_MOD
    SCHEDULER --- V_HF
    API -..->|":ro"| V_MOD
    PROM --- V_PROM
    GRAF --- V_GRAF
    BACKFILL --- V_HF
```

---

## 3. Data Flow

This diagram traces a complete data journey: from a cron tick that fires every 15 minutes all the way to a prediction rendered on the dashboard. Reading top-to-bottom, you can see two distinct cadences: the **intra-day loop** (everything above the dashed line) repeats every 15 minutes, while the **nightly pipeline** (below the dashed line) runs once per day after midnight UTC. The dashed boundary makes it obvious that the model can only improve once per day, even though fresh price data arrives continuously.

```mermaid
flowchart TD
    CRON["cron fires\n:00 :15 :30 :45 each hour"]

    subgraph intraday["Intra-day loop — every 15 minutes"]
        direction TB
        CG_W["coingecko worker\nfetch_latest()"]
        YF_W["yahoo worker\nfetch_latest()"]
        NA_W["newsapi worker\nfetch_latest()"]
        OC_W["onchain worker\nfetch_latest()"]
        SENT_W["sentiment worker\nVADER + FinBERT\nbatch=32"]

        PG_PRICES[("prices\nmarket_data\nonchain_metrics")]
        PG_NEWS[("news_raw\nnews_sentiment")]
    end

    subgraph nightly["Nightly pipeline — 00:30, 01:00, 01:30 UTC"]
        direction TB
        FEAT_B["feature builder\nbuild_features()"]
        TRAINER["trainer\nxgboost.train()"]
        ARTIFACT[("model_artifacts\nvolume")]
        PREDICTOR["predictor\npredict()"]

        PG_FEAT[("features\npredictions\nmodel_metadata")]
    end

    subgraph serving["Serving — always on"]
        FASTAPI["api (FastAPI)\n:8000"]
        DASH["dashboard\nindex.html"]
    end

    CRON --> CG_W & YF_W & NA_W & OC_W
    CG_W & YF_W & OC_W -->|"ON CONFLICT DO NOTHING"| PG_PRICES
    NA_W -->|"ON CONFLICT (url)"| PG_NEWS
    PG_NEWS -->|"WHERE sentiment_processed=false"| SENT_W
    SENT_W -->|"scores + flag update"| PG_NEWS

    PG_PRICES & PG_NEWS -->|"00:30 UTC"| FEAT_B
    FEAT_B -->|"upsert"| PG_FEAT
    PG_FEAT -->|"01:00 UTC"| TRAINER
    TRAINER -->|"model_version.json"| ARTIFACT
    TRAINER -->|"metrics + is_active=true"| PG_FEAT
    ARTIFACT -->|"01:30 UTC"| PREDICTOR
    PREDICTOR -->|"INSERT predictions"| PG_FEAT

    PG_PRICES & PG_FEAT & PG_NEWS -->|"live queries"| FASTAPI
    FASTAPI -->|"GET /"| DASH
```

---

## 4. Database Schema

This ER diagram shows every table and all meaningful relationships. The `features` table is wide (29 columns) — only representative columns are shown; refer to the Alembic migration `001_initial_schema.py` for the full list. A design note: `forex`, `commodity`, and `index` instruments share a single `market_data` table distinguished by a `category` column, rather than three separate tables. This simplifies join patterns in feature engineering at the cost of a slightly wider index scan per category filter; the tradeoff is documented in the README.

```mermaid
erDiagram
    prices {
        bigint id PK
        varchar symbol
        varchar interval
        timestamptz timestamp
        numeric open
        numeric high
        numeric low
        numeric close
        numeric volume
        varchar source
    }

    market_data {
        bigint id PK
        varchar symbol
        varchar interval
        varchar category
        timestamptz timestamp
        numeric open
        numeric high
        numeric low
        numeric close
        numeric volume
        varchar source
    }

    onchain_metrics {
        bigint id PK
        varchar metric_name
        timestamptz timestamp
        float value
    }

    news_raw {
        bigint id PK
        varchar query
        varchar headline
        text description
        varchar source_name
        varchar url
        timestamptz published_at
        boolean sentiment_processed
    }

    news_sentiment {
        bigint id PK
        bigint news_id FK
        float vader_compound
        float vader_pos
        float vader_neg
        float vader_neu
        float finbert_positive
        float finbert_negative
        float finbert_neutral
        varchar finbert_label
        timestamptz processed_at
    }

    features {
        bigint id PK
        date date
        float btc_close
        float btc_return_1d
        float btc_volatility_7d
        float btc_rsi_14
        float btc_macd
        float btc_bb_position
        float eth_return_1d
        float gold_return_1d
        float vader_mean
        float finbert_mean
        int article_count
        int day_of_week
        int days_since_halving
        float target_next_close
        float target_next_log_return
    }

    predictions {
        bigint id PK
        date prediction_date
        date target_date
        float predicted_price
        float predicted_log_return
        float confidence_lower
        float confidence_upper
        varchar model_version
        float actual_price
        timestamptz created_at
    }

    model_metadata {
        bigint id PK
        varchar version
        timestamptz trained_at
        int train_rows
        int val_rows
        float rmse
        float mae
        float mape
        float directional_accuracy
        float naive_rmse
        json feature_importances
        varchar artifact_path
        boolean is_active
    }

    job_runs {
        bigint id PK
        varchar job_name
        varchar status
        timestamptz started_at
        timestamptz finished_at
        text error_message
        int rows_processed
    }

    news_raw ||--o{ news_sentiment : "scored by"
```

---

## 5. Scheduling Timeline

This Gantt chart shows the daily cron schedule. The first two blocks represent two consecutive 15-minute ingestion windows to make the stagger pattern visible — in reality this pattern repeats 96 times per day. Workers are offset by 2 minutes to prevent simultaneous API calls that would breach free-tier rate limits. The nightly pipeline runs sequentially: the feature builder must complete before the trainer starts, and the trainer must complete before the predictor runs. The 30-minute gap between trainer start (01:00) and predictor start (01:30) is intentional slack — on a Raspberry Pi 5, XGBoost training on 5+ years of daily data typically completes in under 5 minutes, but FinBERT backfill during the sentiment step can take longer on first run.

```mermaid
gantt
    title Daily Cron Schedule (UTC)
    dateFormat HH:mm
    axisFormat %H:%M

    section Window :00 (repeated ×96/day)
    coingecko           :cg0, 00:00, 1m
    yahoo               :yf0, 00:02, 1m
    newsapi             :na0, 00:04, 1m
    onchain             :oc0, 00:06, 1m
    sentiment worker    :sw0, 00:08, 3m

    section Window :15 (pattern repeat)
    coingecko           :cg1, 00:15, 1m
    yahoo               :yf1, 00:17, 1m
    newsapi             :na1, 00:19, 1m
    onchain             :oc1, 00:21, 1m
    sentiment worker    :sw1, 00:23, 3m

    section Nightly Pipeline (sequential, depends on prior step)
    feature builder     :crit, fb, 00:30, 20m
    trainer             :crit, tr, 01:00, 25m
    predictor           :crit, pr, 01:30, 5m
```

---

## 6. Model Training and Prediction Sequence

This sequence diagram covers the nightly model lifecycle, including the critical failure branch. The key invariant is that **the active model never regresses to an untrained state**: if training fails, the database retains the previous `is_active=true` row and the predictor simply reuses it. The only way to end up with no active model is on a fresh install before the first successful training run, which is why the predictor raises a clear error (`No trained model found`) rather than producing a garbage prediction.

```mermaid
sequenceDiagram
    autonumber
    participant CRON as scheduler (cron)
    participant FB as feature builder
    participant TRAINER as trainer
    participant FS as model_artifacts (volume)
    participant PRED as predictor
    participant DB as postgres

    Note over CRON: 00:30 UTC — nightly pipeline begins

    CRON ->> FB: python -m src.features.builder
    activate FB
    FB ->> DB: SELECT prices, market_data, onchain_metrics, news_sentiment
    DB -->> FB: raw time series
    FB ->> FB: compute 29 features, shift target -1 day
    FB ->> DB: INSERT INTO features ON CONFLICT (date) DO UPDATE
    FB -->> CRON: exit 0 (or exit 1 on failure)
    deactivate FB

    Note over CRON: 01:00 UTC

    CRON ->> TRAINER: python -m src.model.trainer
    activate TRAINER
    TRAINER ->> DB: SELECT * FROM features WHERE target_next_close IS NOT NULL

    alt insufficient data (< 60 rows)
        TRAINER -->> CRON: exit 1 — previous active model unchanged
    else enough data
        DB -->> TRAINER: feature rows
        TRAINER ->> TRAINER: 80/20 time-series split
        TRAINER ->> TRAINER: XGBRegressor.fit() — CPU, hist tree method
        TRAINER ->> TRAINER: compute RMSE, MAE, MAPE, directional accuracy vs naive

        alt training raises exception
            TRAINER -->> CRON: exit 1 — DB untouched, previous model stays active
        else training succeeds
            TRAINER ->> FS: save model_{version}.json
            TRAINER ->> FS: overwrite latest.json pointer
            TRAINER ->> FS: prune versions beyond last 10
            TRAINER ->> DB: UPDATE model_metadata SET is_active=false (previous)
            TRAINER ->> DB: INSERT INTO model_metadata (is_active=true)
            TRAINER -->> CRON: exit 0
        end
    end
    deactivate TRAINER

    Note over CRON: 01:30 UTC

    CRON ->> PRED: python -m src.model.predictor
    activate PRED
    PRED ->> FS: read latest.json → load model artifact

    alt latest.json missing (no trained model)
        PRED -->> CRON: exit 1 — FileNotFoundError logged
    else model loaded
        PRED ->> DB: SELECT * FROM features ORDER BY date DESC LIMIT 1
        DB -->> PRED: latest feature row
        PRED ->> PRED: XGBRegressor.predict() → price + 95% CI
        PRED ->> DB: INSERT INTO predictions ON CONFLICT DO UPDATE
        PRED ->> DB: UPDATE predictions SET actual_price=... (fill yesterday)
        PRED -->> CRON: exit 0
    end
    deactivate PRED

    Note over CRON: nightly pipeline complete
```

---

## 7. API and Dashboard Request Flow

This sequence shows what happens when a user opens the dashboard. The browser first fetches the static HTML, then fires five parallel API calls to populate each panel. Each API endpoint executes a single SQL query against Postgres — there is no caching layer in the current design. If a query returns no rows (e.g., no predictions exist yet because training hasn't run), the endpoint returns HTTP 503 with a human-readable message, and the dashboard panel shows a loading placeholder rather than crashing.

```mermaid
sequenceDiagram
    autonumber
    participant B as Browser
    participant API as api (FastAPI :8000)
    participant DB as postgres

    B ->> API: GET /
    API -->> B: 200 index.html (static)

    Note over B: JavaScript fires parallel fetch() calls

    par Price panel
        B ->> API: GET /api/price/current
        API ->> DB: SELECT close FROM prices WHERE symbol='BTC' ORDER BY timestamp DESC LIMIT 1
        DB -->> API: row or empty
        alt no data
            API -->> B: 503 {"detail":"No price data available yet"}
        else
            API -->> B: 200 {"symbol":"BTC","price":...,"timestamp":...}
        end
    and Prediction panel
        B ->> API: GET /api/prediction/latest
        API ->> DB: SELECT ... FROM predictions ORDER BY prediction_date DESC LIMIT 1
        DB -->> API: row or empty
        API -->> B: 200 PredictionResponse or 503
    and Multi-asset overlay + correlation heatmap
        B ->> API: GET /api/correlations?days=90&window=30
        API ->> DB: SELECT prices (BTC+ETH), market_data, onchain_metrics, news_sentiment
        DB -->> API: time series rows
        API ->> API: pivot → pct change → rolling Pearson corr
        API -->> B: 200 {series:{...}, correlation_matrix:{...}}
    and Sentiment chart
        B ->> API: GET /api/sentiment/daily?days=60
        API ->> DB: SELECT AVG(vader_compound), COUNT(*) GROUP BY date
        DB -->> API: daily aggregates
        API -->> B: 200 [{date, vader_mean, finbert_mean, article_count}]
    and Model card
        B ->> API: GET /api/model/metadata
        API ->> DB: SELECT * FROM model_metadata WHERE is_active=true LIMIT 1
        DB -->> API: metadata row or empty
        API -->> B: 200 ModelMetadataResponse or 503
    end

    Note over B: Chart.js renders panels; auto-refresh every 5 minutes
```

---

## 8. Observability Flow

This diagram shows how metrics, dashboards, and alerts are wired together. The `api` service is the only application that currently exposes `/metrics` — all other services (scheduler, sentiment worker, etc.) write job outcome data to the `job_runs` table in Postgres rather than exposing a separate scrape endpoint. This is a deliberate simplicity tradeoff: adding per-job Prometheus exporters would require each cron job to bind a port, which is awkward for ephemeral processes. If you want richer per-job metrics in the future, consider adding a Pushgateway.

```mermaid
flowchart LR
    subgraph Services["Application Services"]
        API["api :8000\n/metrics (Prometheus format)"]
        PY["scheduler jobs\nwrites to job_runs table"]
    end

    subgraph Observability["Observability Stack"]
        PROM["prometheus :9090\nScrapes every 15s\nRetains 30 days"]
        GRAF["grafana :3000\nAuto-provisioned dashboard\nBTC Pipeline panel set"]
        RULES["alert_rules.yml\n• JobFailed\n• DataStale >30min\n• API 5xx >1%\n• PredictionError >2×avg"]
        WEBHOOK["Alert Webhook\n(Discord / Telegram / email)\nconfigure ALERT_WEBHOOK_URL"]
    end

    API -->|"HTTP scrape /metrics"| PROM
    PY -.->|"SQL (indirect — no scrape)"| API

    PROM -->|"evaluate alert rules every 15s"| RULES
    RULES -->|"fires if threshold breached"| WEBHOOK

    PROM -->|"PromQL datasource"| GRAF

    subgraph Key_Metrics["Key Metrics Tracked"]
        M1["btc_pipeline_job_runs_total\n{job, status}"]
        M2["btc_pipeline_last_ingestion_timestamp_seconds\n{source}"]
        M3["btc_pipeline_model_rmse"]
        M4["btc_pipeline_prediction_error_usd"]
        M5["http_request_duration_seconds\n(via prometheus-fastapi-instrumentator)"]
    end

    API --- M1 & M2 & M3 & M4 & M5
```

---

## 9. Deployment Topology

These two diagrams show the same Docker Compose stack in the two environments it is designed to run in. The images are identical — the only difference is the CPU architecture. Multi-arch images are built with `docker buildx` on a developer machine and pushed to a registry; the Pi pulls and runs the ARM64 layer automatically. Memory limits in `docker-compose.yml` are tuned for the Pi's 8 GB, which means they are conservative on a laptop (you may want to raise them locally if you are running other heavy workloads).

### Local development (x86\_64 / Apple Silicon)

```mermaid
flowchart TD
    subgraph Laptop["Developer Laptop (Docker Desktop)"]
        direction TB
        subgraph Compose["docker compose up"]
            PG[("postgres\n:5432")]
            SCHED["scheduler"]
            API["api\n:8000"]
            PROM["prometheus\n:9090"]
            GRAF["grafana\n:3000"]
        end
        BROWSER["Browser\nlocalhost:8000"]
        VSCODE["VS Code\nFile edits (bind mount optional)"]
    end

    BROWSER -->|"HTTP"| API
    VSCODE -.->|"rebuild on change"| Compose
    API --> PG
    SCHED --> PG
    PROM -->|"scrape"| API
    GRAF -->|"PromQL"| PROM
```

### Raspberry Pi 5 deployment (ARM64, 8 GB RAM)

```mermaid
flowchart TD
    subgraph Pi["Raspberry Pi 5 — ARM64 (8 GB)"]
        direction TB
        subgraph Compose["docker compose up (pulled ARM64 layers)"]
            PG[("postgres\n:5432")]
            SCHED["scheduler\nFinBERT CPU-only\ntorch ARM64 wheel"]
            API["api\n:8000"]
            PROM["prometheus\n:9090"]
            GRAF["grafana\n:3000"]
        end
    end

    subgraph LAN["Home LAN"]
        PHONE["Any browser\npi-hostname:8000"]
        LAPTOP["Laptop\npi-hostname:3000 (Grafana)"]
    end

    subgraph Registry["Container Registry"]
        IMG["btc-pipeline:latest\n(linux/amd64 + linux/arm64)"]
    end

    IMG -->|"docker compose pull"| Pi
    PHONE -->|"HTTP over LAN"| API
    LAPTOP -->|"HTTP over LAN"| GRAF
```

> **Pi resource notes:** `torch` is installed from `https://download.pytorch.org/whl/cpu` so no CUDA layer is pulled. Memory limits in `docker-compose.yml` cap the scheduler at 1.5 GB (headroom for simultaneous FinBERT inference + XGBoost training). The API is capped at 512 MB. Raise limits if you see OOM kills in `docker stats`.

---

## Updating These Diagrams

When you change the system, update these diagrams **in the same PR** as the code change. Here is which diagram owns which concern:

| Concern | Diagram(s) to update |
|---|---|
| Adding a new data source | §1 System Context, §3 Data Flow, §4 DB Schema (if new table), §5 Scheduling Timeline |
| Adding or renaming a Docker service | §2 Container Diagram, §9 Deployment Topology |
| Changing the DB schema | §4 Database Schema |
| Changing the cron schedule | §5 Scheduling Timeline |
| Changing training / prediction logic | §6 Model Training Sequence |
| Adding a new API endpoint | §7 API Request Flow |
| Adding new Prometheus metrics or alert rules | §8 Observability Flow |
| Changing port mappings or volume mounts | §2 Container Diagram, §9 Deployment Topology |

Mermaid diagrams render natively in GitHub pull requests, VS Code (with the Markdown Preview Mermaid Support extension), and `mdBook`. To preview locally without pushing, open `docs/ARCHITECTURE.md` in VS Code and use **Ctrl+Shift+V** (or **Cmd+Shift+V** on macOS).
