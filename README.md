# 🎲 Story Forge

A self-hosted, single-player AI text RPG that runs on your own Claude subscription. Claude Code interviews you to
design a world (setting, rules, combat, your character), writes the rules and Python scripts for its mechanics,
then runs the game as a game master. Every world can work completely differently: a D&D-style dungeon crawl today,
a hero academy or a vampire chronicle tomorrow.

## Quick start

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/) and a Claude Pro or Max subscription.

1. Download this repo (**Code → Download ZIP**, then unpack it) or `git clone` it.
2. Start it:
   - **Windows:** double-click `start.bat`
   - **macOS / Linux:** run `./start.sh`
3. The browser opens http://localhost:8000. Click **Connect Claude account**, sign in, and paste the code back.
4. Click **+ New world**.

`start.bat` / `start.sh` also download updates each time they run. Stop the app with `stop.bat` or `docker compose down`.

## Moving worlds between devices

Worlds are stored inside Docker (the `worlds` volume). Use **Export** on a world card to download it as a `.zip`, and
**Import world** on the other device. Exporting is also how you back up a world.

## Optional settings

Copy `.env.example` to `.env`, then run `start.bat` again. A plain container restart does **not** re-read `.env`.

- `CLAUDE_MODEL=sonnet` makes turns faster and uses less of your plan's limits.

## How it works

```
Browser ──WebSocket──► FastAPI (app/) ──► claude -p --resume <session>   (cwd = /app/worlds/<id>)
                                               │  Bash: python3 -m dice / -m db / scripts/*.py
                                               ▼
                                     <world>/  WORLD.md, rules/, scripts/, world.db, memory/
```

- **World builder** (`engine/prompts/build.md`): an interview that writes `WORLD.md`, `rules/*.md` and `scripts/*.py`, tests the scripts and fills `world.db`.
- **Game master** (`engine/prompts/play.md`): resolves actions through scripts and dice, stores every change in `world.db`, and keeps `memory/recap.md` up to date so long campaigns stay consistent.
- **Shared tools** (`engine/lib/`): `dice.py` (dice expressions) and `db.py` (generic entities/relations/events store).
- Claude runs inside the container with full permissions, so it can work without prompts but can't touch your computer.

## Tips

- Talk to the GM out of character with `ooc: ...`, e.g. `ooc: from now on, using a quirk costs 10 stamina`.
- To change a finished world, open its **World builder** tab and ask.

## Development

```
docker compose up -d --build     # build from source instead of downloading the image
docker compose logs -f
docker compose exec storyforge sh   # look inside; worlds are in /app/worlds
```

Changes to `engine/prompts/` only apply to **new** Claude conversations, because the system prompt is fixed per session.
Pushing to `main` publishes a new image to `ghcr.io` through GitHub Actions.
