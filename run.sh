#!/usr/bin/env bash
set -e

echo "Syncing dependencies..."
uv sync

echo "Starting server → http://localhost:8000/docs"
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload
