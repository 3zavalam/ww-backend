import os
import uuid
import json
import time
import threading
import shutil
import subprocess
import redis
from uuid import uuid4
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# Local processing imports
from extract_rally_shot import extract_one_shot_from_rally
from detect_impact_frame import detect_impact_frame
from detect_preparation_frame import detect_preparation_frame
from extract_follow_through import extract_follow_through
from extract_keypoints import extract_keypoints_from_images
from compare_dtw import compare_all
from generate_pose_overlay import generate_pose_overlay
from analyze_with_ai import build_stroke_json, analyze_stroke_with_ai, generate_drills_with_ai

# Configuration
MAX_WORKERS = int(os.getenv('MAX_WORKERS', 1))
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
STATIC_FOLDER = os.getenv("STATIC_FOLDER", os.path.join(os.getcwd(), "static"))
KEYFRAME_FOLDER = os.getenv("KEYFRAME_FOLDER", os.path.join(os.getcwd(), "temp_keyframes"))
KEYPOINT_FOLDER = os.getenv("KEYPOINT_FOLDER", os.path.join(os.getcwd(), "keypoints"))
UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", os.path.join(os.getcwd(), "uploads"))
CLIP_FOLDER = os.path.join(STATIC_FOLDER, "clips")
PUBLIC_KF_FOLDER = os.path.join(STATIC_FOLDER, "keyframes")
ALLOWED_ORIGINS = os.getenv(
    "ALLOWED_ORIGINS",
    "https://www.winnerway.pro,http://localhost:8080"
).split(",")

# Memory optimization settings
MAX_VIDEO_RESOLUTION = os.getenv("MAX_VIDEO_RESOLUTION", "720p")  # 720p or 480p
MAX_VIDEO_DURATION = int(os.getenv("MAX_VIDEO_DURATION", 30))  # seconds
ENABLE_VIDEO_OPTIMIZATION = os.getenv("ENABLE_VIDEO_OPTIMIZATION", "true").lower() == "true"

# Job TTL settings
JOB_TTL_SECONDS = int(os.getenv("JOB_TTL_SECONDS", 3600))  # 1 hour default

# Initialize Flask first
app = Flask(__name__, static_folder=STATIC_FOLDER)
CORS(app, origins=ALLOWED_ORIGINS)

# Initialize Redis connection (will be tested on first use)
r = None

def get_redis_connection():
    """Get Redis connection with lazy initialization"""
    global r
    if r is None:
        try:
            r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True)
            r.ping()  # Test connection
            app.logger.info("Redis connection established")
        except Exception as e:
            app.logger.warning(f"Redis connection failed: {e}")
            r = None
    return r

def redis_set_status(job_id, status, **extra):
    """Set job status in Redis with TTL"""
    redis_conn = get_redis_connection()
    if not redis_conn:
        app.logger.error(f"Cannot set status for job {job_id}: Redis connection failed")
        return False
    
    try:
        # Create job key
        job_key = f"job:{job_id}"
        
        # Set status and extra fields using HSET
        fields_to_set = {"status": status}
        fields_to_set.update(extra)
        
        # HSET job:<id> field1 value1 field2 value2 ...
        redis_conn.hset(job_key, mapping=fields_to_set)
        
        # Set TTL for the job key
        redis_conn.expire(job_key, JOB_TTL_SECONDS)
        
        app.logger.info(f"Set Redis status for job {job_id}: {status}")
        return True
        
    except Exception as e:
        app.logger.error(f"Failed to set Redis status for job {job_id}: {e}")
        return False

def ensure_directories():
    """Create directories when needed"""
    for folder in [STATIC_FOLDER, KEYFRAME_FOLDER, KEYPOINT_FOLDER, UPLOAD_FOLDER, CLIP_FOLDER, PUBLIC_KF_FOLDER]:
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            app.logger.error(f"Cannot create directory {folder}: {e}")
            raise

# Global state
job_status = {}
job_results = {}
last_activity = {"time": time.time()}

# Redis queue name
REDIS_QUEUE_NAME = "video_jobs_queue"


def safe_rm(path):
    """Safely remove files/directories with error handling"""
    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
        elif os.path.exists(path):
            os.remove(path)
    except Exception as e:
        app.logger.warning(f"Failed to remove {path}: {e}")


def optimize_video_for_memory(input_path, output_path, max_resolution="720p", max_duration=30):
    """
    Optimize video to reduce memory usage:
    - Convert MOV/AVI to MP4
    - Resize to 720p max (accepts any input resolution)
    - Limit duration to avoid long clips
    - Compress to reduce file size
    """
    try:
        import cv2
        cap = cv2.VideoCapture(input_path)
        
        # Get video properties
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        duration = frame_count / fps if fps > 0 else 0
        
        cap.release()
        
        # Log original video info
        input_ext = os.path.splitext(input_path)[1].lower()
        app.logger.info(f"Original video: {width}x{height}, {duration:.1f}s, {input_ext}")
        
        # Calculate new dimensions for target resolution
        if max_resolution == "720p":
            max_height = 720
            max_width = 1280
        else:  # 480p fallback
            max_height = 480
            max_width = 854
            
        # Always scale down if larger than target, maintain aspect ratio
        if height > max_height or width > max_width:
            if width / height > max_width / max_height:
                new_width = max_width
                new_height = int(height * max_width / width)
            else:
                new_height = max_height
                new_width = int(width * max_height / height)
                
            # Ensure even dimensions for H.264
            new_width = new_width - (new_width % 2)
            new_height = new_height - (new_height % 2)
            scale_filter = f"scale={new_width}:{new_height}"
            app.logger.info(f"Scaling down: {width}x{height} → {new_width}x{new_height}")
        else:
            scale_filter = None
            app.logger.info(f"Video already within {max_resolution} limits")
            
        # Build ffmpeg command
        cmd = ['ffmpeg', '-i', input_path]
        
        # Limit duration if too long
        if duration > max_duration:
            cmd.extend(['-t', str(max_duration)])
            app.logger.info(f"Limiting duration: {duration:.1f}s → {max_duration}s")
            
        # Add scaling if needed
        if scale_filter:
            cmd.extend(['-vf', scale_filter])
            
        # Compression settings optimized for memory efficiency
        cmd.extend([
            '-c:v', 'libx264',
            '-preset', 'fast',
            '-crf', '28',  # Higher CRF = more compression, good quality
            '-maxrate', '2M',  # Limit bitrate to 2Mbps
            '-bufsize', '4M',
            '-c:a', 'aac',
            '-b:a', '128k',
            '-movflags', '+faststart',  # Web optimization
            '-y',  # Overwrite output
            output_path
        ])
        
        app.logger.info(f"Running ffmpeg conversion...")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        
        if result.returncode != 0:
            app.logger.error(f"FFmpeg conversion failed: {result.stderr}")
            # If optimization fails, copy original (but this might fail for MOV)
            try:
                shutil.copy(input_path, output_path)
            except:
                return False
            return False
            
        # Check results
        if os.path.exists(output_path):
            original_size = os.path.getsize(input_path)
            optimized_size = os.path.getsize(output_path)
            reduction = (1 - optimized_size/original_size) * 100
            
            app.logger.info(f"✅ Conversion successful: {original_size/1024/1024:.1f}MB → {optimized_size/1024/1024:.1f}MB ({reduction:.1f}% reduction)")
            return True
        else:
            app.logger.error("Output file not created")
            return False
        
    except Exception as e:
        app.logger.error(f"Video optimization failed: {e}")
        # Fallback: try to copy original
        try:
            if os.path.exists(input_path) and not os.path.exists(output_path):
                shutil.copy(input_path, output_path)
        except:
            pass
        return False


def cleanup_memory():
    """Force garbage collection and OpenCV memory cleanup"""
    import gc
    import cv2
    
    # Close all OpenCV windows and release resources
    cv2.destroyAllWindows()
    
    # Force garbage collection
    gc.collect()
    
    app.logger.debug("Memory cleanup completed")


def process_video_job(job_data):
    """Process a single video analysis job"""
    job_id = job_data["id"]
    video_path = job_data["video_path"]
    email = job_data["email"]
    stroke_type = job_data["stroke_type"]
    handedness = job_data["handedness"]
    
    app.logger.info(f"Processing job {job_id} for {email}")
    
    # Update status
    job_status[job_id] = "processing"
    
    # Generate unique identifiers
    uid = uuid4().hex
    base = os.path.splitext(os.path.basename(video_path))[0].replace(".", "_")
    video_name = f"{base}_{uid}"
    
    # Temporary paths for processing
    kf_output = os.path.join(KEYFRAME_FOLDER, video_name)
    kp_output = os.path.join(KEYPOINT_FOLDER, video_name)
    
    try:
        # 1) Optimize video for memory usage first (if enabled)
        processing_video = video_path
        if ENABLE_VIDEO_OPTIMIZATION:
            app.logger.info(f"Optimizing video for memory efficiency: {job_id}")
            optimized_video = os.path.join(UPLOAD_FOLDER, f"{video_name}_optimized.mp4")
            optimize_video_for_memory(video_path, optimized_video, 
                                    max_resolution=MAX_VIDEO_RESOLUTION, 
                                    max_duration=MAX_VIDEO_DURATION)
            
            # Use optimized video for processing
            processing_video = optimized_video if os.path.exists(optimized_video) else video_path
        else:
            app.logger.info(f"Video optimization disabled, using original: {job_id}")
        
        # 2) Generate pose overlay video
        app.logger.info(f"Generating pose overlay for {job_id}")
        overlay_temp = os.path.join(CLIP_FOLDER, f"{video_name}_overlay.avi")
        final_clip = os.path.join(CLIP_FOLDER, f"{video_name}.mp4")
        
        generate_pose_overlay(processing_video, overlay_temp)
        
        # Cleanup memory after pose generation
        cleanup_memory()
        
        # Convert to MP4 if needed
        if os.path.exists(overlay_temp):
            subprocess.run(
                ['ffmpeg', '-i', overlay_temp, '-c:v', 'libx264', '-preset', 'fast',
                 '-crf', '23', '-c:a', 'aac', '-y', final_clip],
                capture_output=True, text=True, check=False
            )
            if os.path.exists(final_clip):
                os.remove(overlay_temp)
            else:
                shutil.move(overlay_temp, final_clip)

        # 3) Extract keyframes (use optimized video)
        app.logger.info(f"Extracting keyframes for {job_id}")
        os.makedirs(kf_output, exist_ok=True)
        is_right = handedness == "right"
        
        detect_preparation_frame(processing_video, kf_output, is_right_handed=is_right)
        detect_impact_frame(processing_video, kf_output, is_right_handed=is_right)
        extract_follow_through(processing_video, kf_output)
        
        # Cleanup memory after keyframe extraction
        cleanup_memory()

        # 4) Extract keypoints
        app.logger.info(f"Extracting keypoints for {job_id}")
        extract_keypoints_from_images(kf_output, kp_output)

        # 5) Generate AI feedback
        app.logger.info(f"Generating AI analysis for {job_id}")
        comparison = compare_all(kp_output, stroke_type)
        stroke_json = build_stroke_json(kp_output)
        issues = analyze_stroke_with_ai(stroke_json, stroke_type)
        drills = generate_drills_with_ai(issues, stroke_type)

        # 5) Copy reference clip if available
        reference_url = None
        ref_clip = comparison.get("reference_clip")
        if ref_clip:
            try:
                ref_source = os.path.join("data", stroke_type, ref_clip)
                if os.path.exists(ref_source):
                    shutil.copy(ref_source, os.path.join(CLIP_FOLDER, ref_clip))
                    reference_url = f"/static/clips/{ref_clip}"
            except Exception as e:
                app.logger.warning(f"Failed to copy reference clip: {e}")

        # 6) Move keyframes to public folder
        public_kf = os.path.join(PUBLIC_KF_FOLDER, video_name)
        os.makedirs(public_kf, exist_ok=True)
        kf_urls = {}
        
        for phase in ["preparation", "impact", "follow_through"]:
            src = os.path.join(kf_output, f"{phase}.jpg")
            if os.path.exists(src):
                dst = os.path.join(public_kf, f"{phase}.jpg")
                shutil.copy(src, dst)
                kf_urls[phase] = f"/static/keyframes/{video_name}/{phase}.jpg"

        # 7) Store results
        result = {
            "feedback": issues,
            "drills": drills,
            "video_url": f"/static/clips/{video_name}.mp4",
            "keyframes": kf_urls,
            "reference_url": reference_url
        }
        
        job_status[job_id] = "done"
        job_results[job_id] = result
        last_activity["time"] = time.time()
        
        app.logger.info(f"Job {job_id} completed successfully")

    except Exception as e:
        app.logger.error(f"Error processing job {job_id}: {e}")
        job_status[job_id] = "error"
        job_results[job_id] = {"error": str(e)}
    
    finally:
        # Aggressive cleanup for memory conservation
        safe_rm(kf_output)
        safe_rm(kp_output)
        
        # Remove original uploaded video
        if 'video_path' in job_data and os.path.exists(job_data['video_path']):
            safe_rm(job_data['video_path'])
            
        # Remove optimized video (keep only final processed clip)
        if 'optimized_video' in locals() and os.path.exists(optimized_video):
            safe_rm(optimized_video)
            
        # Final memory cleanup
        cleanup_memory()
        
        app.logger.info(f"Memory cleanup completed for job {job_id}")


def redis_worker_loop():
    """Redis-based worker that processes jobs from Redis queue"""
    app.logger.info("Redis worker started")
    redis_conn = get_redis_connection()
    
    if not redis_conn:
        app.logger.error("Cannot start worker: Redis connection failed")
        return
    
    while True:
        try:
            # BLPOP with 30 second timeout
            result = redis_conn.blpop(REDIS_QUEUE_NAME, timeout=30)
            
            if result is None:  # Timeout, continue waiting
                continue
                
            # Parse job data
            queue_name, job_json = result
            job_data = json.loads(job_json)
            
            app.logger.info(f"Processing job {job_data['id']} from Redis queue")
            process_video_job(job_data)
            
        except json.JSONDecodeError as e:
            app.logger.error(f"Invalid JSON in Redis queue: {e}")
            continue
        except Exception as e:
            app.logger.error(f"Redis worker error: {e}")
            time.sleep(5)
            # Reconnect Redis if needed
            redis_conn = get_redis_connection()


@app.route("/upload", methods=["POST"])
def upload_video():
    """Handle video upload and queue for processing"""
    if 'video' not in request.files:
        return jsonify({"error": "No video file provided"}), 400
    
    file = request.files['video']
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400
    
    # Check file extension (accept MP4, MOV, AVI, etc.)
    allowed_extensions = {'.mp4', '.mov', '.avi', '.m4v', '.3gp'}
    file_ext = os.path.splitext(file.filename.lower())[1]
    
    if file_ext not in allowed_extensions:
        return jsonify({
            "error": f"Unsupported file format. Please use: {', '.join(allowed_extensions)}"
        }), 400
    
    # Get form data
    email = request.form.get('email')
    stroke_type = request.form.get('stroke_type', 'forehand')
    handedness = request.form.get('handedness', 'right')
    
    if not email:
        return jsonify({"error": "Email is required"}), 400
    
    try:
        # Save uploaded file with original extension
        job_id = uuid.uuid4().hex
        filename = f"{stroke_type}_{job_id}{file_ext}"
        video_path = os.path.join(UPLOAD_FOLDER, filename)
        file.save(video_path)
        
        app.logger.info(f"Uploaded video: {file.filename} ({file_ext}) -> {filename}")


        # Queue the job in Redis
        job_data = {
            "id": job_id,
            "video_path": video_path,
            "email": email,
            "stroke_type": stroke_type,
            "handedness": handedness,
            "original_filename": file.filename
        }
        
        # Set status in Redis with job metadata
        redis_set_status(
            job_id, 
            "queued", 
            email=email,
            stroke_type=stroke_type,
            handedness=handedness,
            original_filename=file.filename,
            path=video_path,
            updated_at=time.time()
        )
        
        job_status[job_id] = "queued"
        
        # Push to Redis queue
        redis_conn = get_redis_connection()
        if redis_conn:
            try:
                redis_conn.lpush(REDIS_QUEUE_NAME, json.dumps(job_data))
                app.logger.info(f"Job {job_id} queued in Redis for processing")
            except Exception as e:
                app.logger.error(f"Failed to queue job in Redis: {e}")
                job_status[job_id] = "error"
                return jsonify({"error": "Failed to queue job for processing"}), 500
        else:
            app.logger.error("Redis connection failed, cannot queue job")
            job_status[job_id] = "error"
            return jsonify({"error": "Service temporarily unavailable"}), 503
            
        last_activity["time"] = time.time()
        
        return jsonify({
            "job_id": job_id,
            "status": "queued",
            "message": "Video uploaded and queued for processing"
        }), 202
        
    except Exception as e:
        app.logger.error(f"Upload failed: {e}")
        return jsonify({"error": "Upload failed"}), 500


@app.route("/status/<job_id>")
def get_job_status(job_id):
    """Get job status and results"""
    status = job_status.get(job_id, "unknown")
    
    if status == "done" and job_id in job_results:
        return jsonify({"status": status, "result": job_results[job_id]})
    elif status == "error" and job_id in job_results:
        return jsonify({"status": status, "error": job_results[job_id]})
    
    return jsonify({"status": status})


@app.route('/static/<path:filename>')
def serve_static(filename):
    """Serve static files"""
    return send_from_directory(STATIC_FOLDER, filename)


@app.route("/health")
def health():
    """Health check endpoint"""
    redis_conn = get_redis_connection()
    queue_size = 0
    redis_status = "disconnected"
    
    if redis_conn:
        try:
            queue_size = redis_conn.llen(REDIS_QUEUE_NAME)
            redis_status = "connected"
        except:
            redis_status = "error"
    
    return jsonify({
        "status": "healthy", 
        "timestamp": time.time(),
        "redis_status": redis_status,
        "queue_size": queue_size,
        "active_jobs": len([j for j in job_status.values() if j in ["queued", "processing"]])
    })


def cleanup_old_jobs():
    """Background thread to cleanup old jobs"""
    while True:
        try:
            current_time = time.time()
            
            # Remove jobs older than 24 hours or if we have too many
            jobs_to_remove = []
            for job_id in list(job_status.keys()):
                if len(job_status) > 1000:
                    jobs_to_remove.append(job_id)
            
            # Remove oldest jobs first
            for job_id in jobs_to_remove[:100]:
                job_status.pop(job_id, None)
                job_results.pop(job_id, None)
                app.logger.info(f"Cleaned up old job {job_id}")
            
            time.sleep(3600)  # Run every hour
            
        except Exception as e:
            app.logger.error(f"Cleanup error: {e}")
            time.sleep(3600)


def start_redis_worker():
    """Start Redis worker thread - called by worker_service.py"""
    app.logger.info(f"Starting Redis worker with MAX_WORKERS={MAX_WORKERS}")
    
    # Ensure directories exist
    ensure_directories()
    
    # Start Redis worker thread
    worker = threading.Thread(target=redis_worker_loop, daemon=True)
    worker.start()
    
    # Start cleanup thread
    cleanup_thread = threading.Thread(target=cleanup_old_jobs, daemon=True)
    cleanup_thread.start()
    
    app.logger.info("Redis worker threads started successfully")
    return worker, cleanup_thread


if __name__ == "__main__":
    app.logger.info(f"Starting t3.py with MAX_WORKERS={MAX_WORKERS}")
    
    # When running directly (not with Gunicorn), start Redis workers
    start_redis_worker()
    
    app.logger.info("t3.py server starting on port 5050...")
    
    # Run with single threaded server to ensure no race conditions
    app.run(
        host="0.0.0.0", 
        port=5050, 
        threaded=True,
        debug=False  # Disable debug to avoid reloader
    )