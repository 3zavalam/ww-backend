# t3.py Usage Guide

## Overview
`t3.py` combines the functionality of `cpu_app.py` and `worker_gpu.py` into a single application that runs everything on one machine with limited concurrency (MAX_WORKERS=1).

## Quick Start

### Option 1: Using the shell script (with Gunicorn)
```bash
./run_t3.sh
```

### Option 2: Direct Python execution
```bash
python3 start_t3.py
```

### Option 3: Manual execution
```bash
# Activate virtual environment
source .venv/bin/activate

# Set environment variables
export MAX_WORKERS=1
export REDIS_HOST=localhost
export REDIS_PORT=6379

# Run the application
python3 t3.py
```

## Configuration

### Environment Variables

#### Core Settings
- `MAX_WORKERS=1` - Number of worker threads (keep at 1)
- `REDIS_HOST=localhost` - Redis host (optional, falls back gracefully)
- `REDIS_PORT=6379` - Redis port
- `STATIC_FOLDER` - Directory for static files
- `UPLOAD_FOLDER` - Directory for uploaded videos
- `KEYFRAME_FOLDER` - Temporary keyframes storage
- `KEYPOINT_FOLDER` - Temporary keypoints storage

#### Memory Optimization (NEW)
- `MAX_VIDEO_RESOLUTION=720p` - Max resolution (720p/480p)
- `MAX_VIDEO_DURATION=30` - Max video duration in seconds
- `ENABLE_VIDEO_OPTIMIZATION=true` - Enable/disable video optimization

### Memory Management
- Single worker ensures no parallel processing conflicts
- Automatic cleanup of temporary files after processing
- OpenCV resources properly released with `cv2.destroyAllWindows()`

### Video Processing Pipeline
1. **Upload**: Accept any supported format (MP4, MOV, AVI, etc.)
2. **Auto-optimize**: Convert to 720p MP4, limit to 30s max
3. **Process**: Generate pose overlay, extract keyframes, analyze
4. **Cleanup**: Remove all temporary files, keep only final results

### Memory Optimization Features
- **Format Conversion**: MOV/AVI → MP4 (iPhone compatibility)
- **Resolution Scaling**: 4K/1080p → 720p (maintains aspect ratio)
- **Duration Limiting**: Long videos truncated to 30 seconds
- **Compression**: H.264 with optimized settings (2Mbps max)
- **Aggressive Cleanup**: Immediate removal of temp files

## API Endpoints

### Upload Video
```
POST /upload
Content-Type: multipart/form-data

Fields:
- video: Video file (MP4, MOV, AVI, M4V, 3GP)
- email: User email
- stroke_type: forehand/backhand/serve
- handedness: right/left
```

**Supported Formats**: MP4, MOV (iPhone), AVI, M4V, 3GP
**Auto-conversion**: All formats converted to optimized MP4

### Check Status
```
GET /status/{job_id}
```

### Health Check
```
GET /health
```

### Static Files
```
GET /static/{path}
```

## Differences from Original Architecture

1. **No AWS/SQS**: Uses local Queue instead of SQS
2. **No EC2 Management**: Removed GPU instance start/stop logic
3. **Local File Storage**: All processing happens locally
4. **Single Worker**: Only one video processed at a time
5. **Graceful Redis**: Redis connection optional, won't crash if unavailable

## Monitoring

The health endpoint shows:
- Queue size
- Active jobs count
- System timestamp

## Troubleshooting

1. **Import Errors**: Ensure virtual environment is activated
2. **Permission Errors**: Check directory write permissions
3. **Port Conflicts**: Change port 5050 if needed
4. **Memory Issues**: Monitor with single worker to prevent overload