#!/usr/bin/env bash
# Runs on the server. Reads the GHCR token from stdin; GHCR_USER comes from the environment.
# Idempotent: safe to re-run for any commit.
set -euo pipefail

cd "$HOME/health-checker"
GHCR_TOKEN="$(cat)"

if ! command -v docker >/dev/null 2>&1; then
  echo "==> Installing Docker"
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER"
fi
D="sudo docker"

if ! swapon --show | grep -q .; then
  echo "==> Creating 1G swap"
  sudo fallocate -l 1G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

echo "==> Logging in to GHCR"
printf '%s' "$GHCR_TOKEN" | $D login ghcr.io -u "$GHCR_USER" --password-stdin

echo "==> Pulling and starting"
$D compose --env-file image.env pull
$D compose --env-file image.env up -d --remove-orphans

echo "==> Waiting for the web UI"
for _ in $(seq 1 30); do
  code="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/ || true)"
  if [ "$code" = "401" ] || [ "$code" = "200" ]; then
    echo "Healthy (HTTP $code)"
    $D logout ghcr.io >/dev/null
    $D image prune -af --filter "until=24h" >/dev/null
    exit 0
  fi
  sleep 2
done

echo "Deploy failed: web UI did not become healthy (last HTTP $code)" >&2
$D compose --env-file image.env logs --tail=100
exit 1
