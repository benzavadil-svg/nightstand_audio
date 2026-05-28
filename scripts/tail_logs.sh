#!/usr/bin/env bash
set -euo pipefail

mkdir -p "$HOME/nightstand-audio/logs"
touch "$HOME/nightstand-audio/logs/nightstand.log"
tail -f "$HOME/nightstand-audio/logs/nightstand.log"
