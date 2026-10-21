#!/usr/bin/env sh
# Story Forge launcher for macOS / Linux: the only file you need (plus Docker).
#   sh storyforge.sh        start Story Forge (and download updates)
#   sh storyforge.sh stop   stop it; your worlds and logins are kept
# Settings: put a .env file (see .env.example in the repo) into ~/.storyforge
set -e
STORYFORGE_HOME="${STORYFORGE_HOME:-$HOME/.storyforge}"
COMPOSE="$STORYFORGE_HOME/launcher-compose.yml"
URL=http://localhost:8000

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed. Get Docker Desktop: https://www.docker.com/products/docker-desktop/"
  exit 1
fi

if [ "$1" = "stop" ]; then
  if docker info >/dev/null 2>&1; then
    docker compose -p storyforge down
    echo "Story Forge stopped. Your worlds are kept."
  else
    echo "Docker is not running, so Story Forge is already stopped."
  fi
  exit 0
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

# Same as docker-compose.yml in the repo, minus the local build. Rewritten on every start.
mkdir -p "$STORYFORGE_HOME"
cat > "$COMPOSE" <<'EOF'
name: storyforge
services:
  storyforge:
    image: ghcr.io/ll0xx/story-forge:latest
    ports:
      - "127.0.0.1:8000:8000"
    env_file:
      - path: .env
        required: false
    volumes:
      - worlds:/app/worlds
      - claude-home:/home/player/.claude
      - codex-home:/home/player/.codex
    restart: unless-stopped
volumes:
  worlds:
  claude-home:
  codex-home:
EOF

echo "Checking for updates..."
docker compose -f "$COMPOSE" pull --quiet >/dev/null 2>&1 || true
echo "Starting Story Forge (the first start can take a few minutes)..."
docker compose -f "$COMPOSE" up -d --remove-orphans

i=0
until curl -s -o /dev/null "$URL/api/health" || [ "$i" -ge 60 ]; do
  i=$((i + 1)); sleep 1
done

if command -v open >/dev/null 2>&1; then open "$URL"
elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1 || true
fi
echo "Story Forge is running at $URL (stop it with: sh storyforge.sh stop)"
