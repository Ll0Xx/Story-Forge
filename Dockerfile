FROM python:3.12-slim

# Node.js (for the Claude Code CLI) + sqlite3 CLI for debugging
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates gnupg sqlite3 \
 && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code && npm cache clean --force

WORKDIR /app
COPY app/requirements.txt app/requirements.txt
RUN pip install --no-cache-dir -r app/requirements.txt

# Claude Code refuses to bypass permissions as root, so run as a normal user.
RUN useradd -m -u 1000 player \
 && mkdir -p /home/player/.claude /app/worlds \
 && chown -R player:player /home/player /app/worlds

COPY --chown=player:player engine engine
COPY --chown=player:player app app
COPY --chown=player:player static static

ENV PYTHONPATH=/app/engine/lib \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ENGINE_DIR=/app/engine \
    WORLDS_DIR=/app/worlds \
    CLAUDE_CONFIG_DIR=/home/player/.claude \
    DISABLE_AUTOUPDATER=1

USER player
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
