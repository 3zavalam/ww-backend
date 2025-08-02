#!/bin/bash

# Winner Way t3.py startup script
# Single worker, single machine deployment

# Activate virtual environment if it exists
if [ -d ".venv" ]; then
    echo "Activating virtual environment..."
    source .venv/bin/activate
elif [ -d "venv" ]; then
    echo "Activating virtual environment..."
    source venv/bin/activate
else
    echo "Warning: No virtual environment found (.venv or venv)"
fi

export MAX_WORKERS=1
export REDIS_HOST=localhost
export REDIS_PORT=6379

# Set local paths relative to current directory
export STATIC_FOLDER="$(pwd)/static"
export KEYFRAME_FOLDER="$(pwd)/temp_keyframes"
export KEYPOINT_FOLDER="$(pwd)/keypoints"
export UPLOAD_FOLDER="$(pwd)/uploads"

# Memory optimization settings for 6GB RAM instance
export MAX_VIDEO_RESOLUTION=720p     # Use 480p for even more memory savings
export MAX_VIDEO_DURATION=30         # Limit video clips to 30 seconds
export ENABLE_VIDEO_OPTIMIZATION=true

# Allow configurable timeout for longer video processing
export TIMEOUT=600

echo "Starting t3.py with single worker configuration..."

# Run with Gunicorn - single worker, limited threads as per recommendations
gunicorn --workers 1 --threads 2 \
        --timeout 600 \
        --bind 0.0.0.0:5050 \
        --worker-class gthread \
        --access-logfile - \
        --error-logfile - \
        t3:app