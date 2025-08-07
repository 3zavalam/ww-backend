#!/usr/bin/env bash
# WinnerWay t3.py startup script  
# For development only - Production should use systemd services

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

echo "=========================================="
echo "⚠️  DEVELOPMENT MODE"
echo "=========================================="
echo "This script runs both Flask API and Worker in development mode."
echo "For production, use systemd services instead:"
echo ""
echo "  sudo cp systemd/*.service /etc/systemd/system/"
echo "  sudo systemctl daemon-reload"
echo "  sudo systemctl enable --now winnerway ww-worker"
echo ""
echo "Starting development services (workers=$MAX_WORKERS, timeout=$TIMEOUT)…"

# Function to cleanup background processes
cleanup() {
    echo ""
    echo "Shutting down development services..."
    if [[ -n $WORKER_PID ]]; then
        echo "Stopping worker service (PID: $WORKER_PID)"
        kill $WORKER_PID 2>/dev/null
        wait $WORKER_PID 2>/dev/null
    fi
    if [[ -n $GUNICORN_PID ]]; then
        echo "Stopping Gunicorn (PID: $GUNICORN_PID)"
        kill $GUNICORN_PID 2>/dev/null
        wait $GUNICORN_PID 2>/dev/null
    fi
    exit 0
}

# Set trap for cleanup on script exit
trap cleanup EXIT INT TERM

# Check Redis connection
echo "🔍 Checking Redis connection..."
if ! python -c "import redis; r=redis.Redis(); r.ping(); print('✅ Redis OK')" 2>/dev/null; then
    echo "❌ Redis connection failed. Make sure Redis is running:"
    echo "   sudo systemctl start redis"
    echo "   # or: redis-server"
    exit 1
fi

# Start the worker service in background
echo "🔄 Starting Redis worker service..."
python worker_service.py &
WORKER_PID=$!
echo "✅ Worker service started (PID: $WORKER_PID)"

# Give worker service time to initialize
sleep 3

# Start Gunicorn Flask app
echo "🚀 Starting Gunicorn Flask app..."
gunicorn t3:app \
  --workers "$MAX_WORKERS" \
  --threads 2 \
  --timeout "$TIMEOUT" \
  --bind 0.0.0.0:5050 \
  --worker-class gthread \
  --access-logfile - \
  --error-logfile - &
GUNICORN_PID=$!

echo "✅ Gunicorn started (PID: $GUNICORN_PID)"
echo ""
echo "📋 Development services running:"
echo "   - Redis Worker: PID $WORKER_PID (BLPOP video_jobs_queue)"
echo "   - Flask API:    PID $GUNICORN_PID (HTTP :5050)"
echo ""
echo "🌐 API available at: http://localhost:5050"
echo "📊 Health check:    http://localhost:5050/health"
echo ""
echo "Press Ctrl+C to stop all services"

# Wait for both processes
wait
