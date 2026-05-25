# ── Stage 1: build Tailwind CSS ───────────────────────────────────────────────
FROM node:20-slim AS css-builder

WORKDIR /build
COPY package.json ./
RUN npm install
COPY tailwind.config.js ./
COPY src/web/static/input.css ./src/web/static/input.css
COPY src/web/templates/ ./src/web/templates/
RUN npm run build:css

# ── Stage 2: runtime image ────────────────────────────────────────────────────
FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    iputils-ping \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --upgrade pip

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN playwright install chromium --with-deps

# Copy pre-built CSS from stage 1
COPY --from=css-builder /build/src/web/static/tailwind.css ./src/web/static/tailwind.css

COPY src/ ./src/
COPY config/ ./config/

RUN mkdir -p data screenshots videos

EXPOSE 8000

CMD ["python3", "-m", "src.main", "--db", "/app/data/monitor.db"]
