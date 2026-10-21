# 🎲 Story Forge

A self-hosted, single-player AI text RPG that runs on your own Claude or ChatGPT subscription. An AI agent
(Claude Code or OpenAI Codex) interviews you to design a world (setting, rules, combat, your character), writes the
rules and Python scripts for its mechanics, then runs the game as a game master. Every world can work completely differently: a D&D-style dungeon crawl today,
a hero academy or a vampire chronicle tomorrow.

## Quick start

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/) and either a Claude Pro or Max
subscription or a paid ChatGPT plan (Plus or higher).

1. Download this repo (**Code → Download ZIP**, then unpack it) or `git clone` it.
2. Start it:
   - **Windows:** double-click `start.bat`
   - **macOS / Linux:** run `./start.sh`
3. The browser opens http://localhost:8000. Pick your AI game master:
   - **Connect Claude:** sign in, then paste the code back into Story Forge.
   - **Connect ChatGPT:** open the link, sign in and enter the code Story Forge shows. It continues by itself.
4. Click **+ New world**. If both accounts are connected, it asks which one runs this world.

You can connect or disconnect accounts later with the status chip in the top bar.

`start.bat` / `start.sh` also download updates each time they run. Stop the app with `stop.bat` or `docker compose down`.

## Moving worlds between devices

Worlds are stored inside Docker (the `worlds` volume). Use **Export** on a world card to download it as a `.zip`, and
**Import world** on the other device. Exporting is also how you back up a world.

## Claude or ChatGPT per world

Each world remembers which AI runs it; the badge on the world card shows it. If both accounts are connected,
click the badge to switch. The new game master starts a fresh conversation and catches up from `WORLD.md`, the
recap and the database, so nothing in the world is lost.

## Optional settings

Copy `.env.example` to `.env`, then run `start.bat` again. A plain container restart does **not** re-read `.env`.

- `CLAUDE_MODEL=sonnet` makes Claude turns faster and uses less of your plan's limits.
- `CODEX_MODEL=...` picks the model for ChatGPT worlds (anything `codex -m` accepts).
- `CLAUDE_CODE_OAUTH_TOKEN` / `CODEX_API_KEY` log in from `.env` instead of the web app.

## How it works

```
Browser ──WebSocket──► FastAPI (app/) ──► claude -p --resume <session>        (cwd = /app/worlds/<id>)
                                           or  codex exec resume <thread> --json
                                               │  shell: python3 -m dice / -m db / scripts/*.py
                                               ▼
                                     <world>/  WORLD.md, rules/, scripts/, world.db, memory/
```

- **World builder** (`engine/prompts/build.md`): an interview that writes `WORLD.md`, `rules/*.md` and `scripts/*.py`, tests the scripts and fills `world.db`.
- **Game master** (`engine/prompts/play.md`): resolves actions through scripts and dice, stores every change in `world.db`, and keeps `memory/recap.md` up to date so long campaigns stay consistent.
- **Shared tools** (`engine/lib/`): `dice.py` (dice expressions) and `db.py` (generic entities/relations/events store).
- **Runner** (`app/runner.py`): one backend per CLI, both turned into the same UI events. The prompts are shared;
  Claude gets them with `--append-system-prompt`, Codex with `-c developer_instructions=...`.
- The agent runs inside the container with full permissions, so it can work without prompts but can't touch your computer.

## Tips

- Talk to the GM out of character with `ooc: ...`, e.g. `ooc: from now on, using a quirk costs 10 stamina`.
- To change a finished world, open its **World builder** tab and ask.

## Development

```
docker compose up -d --build     # build from source instead of downloading the image
docker compose logs -f
docker compose exec storyforge sh   # look inside; worlds are in /app/worlds
```

Changes to `engine/prompts/` only apply to **new** conversations, because the system prompt is fixed per session.
Pushing to `main` publishes a new image to `ghcr.io` through GitHub Actions.
