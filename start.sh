#!/usr/bin/env sh
# Starts Story Forge using this folder for settings (.env). Same as launcher/storyforge.sh.
DIR="$(cd "$(dirname "$0")" && pwd)"
STORYFORGE_HOME="$DIR" exec sh "$DIR/launcher/storyforge.sh" "$@"
