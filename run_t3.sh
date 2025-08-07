#!/usr/bin/env bash
# WinnerWay t3.py startup script
# Single-worker deployment for t3.large

# ── activate venv ──
if [ -d ".venv" ]; then
  echo "Activating .venv…"
  source .venv/bin/activate
elif [ -d "venv" ]; then
  echo "Activating venv…"
  source venv/bin/activate
else
  echo "⚠️  No virtual environment found (.venv or venv)"
fi

# ── load env vars ──
if [ -f ".env" ]; then
  export $(grep -v '^#' .env | xargs)
fi

# ── runtime tweaks ──
export MAX_WORKERS=${MAX_WORKERS:-1}      # default 1
export TIMEOUT=${TIMEOUT:-600}            # default 600 s

echo "Starting t3.py (workers=$MAX_WORKERS, timeout=$TIMEOUT)…"

gunicorn t3:app \
  --workers "$MAX_WORKERS" \
  --threads 2 \
  --timeout "$TIMEOUT" \
  --bind 0.0.0.0:5050 \
  --worker-class gthread \
  --access-logfile - \
  --error-logfile -
