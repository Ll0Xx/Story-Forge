#!/usr/bin/env sh
# Story Forge launcher for macOS / Linux
set -e
cd "$(dirname "$0")"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed. Get Docker Desktop: https://www.docker.com/products/docker-desktop/"
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Starting Docker..."
  if [ "$(uname)" = "Darwin" ]; then open -a Docker; fi
  i=0
  until docker info >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "$i" -ge 60 ]; then echo "Docker did not start. Start it manually and run this again."; exit 1; fi
    sleep 3
  done
fi

echo "Checking for updates..."
docker compose pull --quiet >/dev/null 2>&1 || true
echo "Starting Story Forge (the first start can take a few minutes)..."
docker compose up -d

i=0
until curl -s -o /dev/null http://localhost:8000/api/health || [ "$i" -ge 60 ]; do
  i=$((i + 1)); sleep 1
done

URL=http://localhost:8000
if command -v open >/dev/null 2>&1; then open "$URL"
elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1 || true
fi
echo "Story Forge is running at $URL (stop it with: docker compose down)"
