#!/usr/bin/env bash
set -euo pipefail

uv run langgraph dev --allow-blocking "$@"
