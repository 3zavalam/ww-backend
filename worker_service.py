#!/usr/bin/env python3
"""
Redis Worker Service for WinnerWay Video Processing
Runs independently from the Flask app to process video analysis jobs using Redis BLPOP
"""
import os
import sys
import time
import json
import signal
import logging
import threading
from threading import Event

# Ensure we're in the right directory and set up environment
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Set environment variables for worker configuration
os.environ['MAX_WORKERS'] = os.environ.get('MAX_WORKERS', '1')
os.environ['REDIS_HOST'] = os.environ.get('REDIS_HOST', 'localhost')
os.environ['REDIS_PORT'] = os.environ.get('REDIS_PORT', '6379')

# Set local paths
current_dir = os.getcwd()
os.environ['STATIC_FOLDER'] = os.environ.get('STATIC_FOLDER', os.path.join(current_dir, 'static'))
os.environ['KEYFRAME_FOLDER'] = os.environ.get('KEYFRAME_FOLDER', os.path.join(current_dir, 'temp_keyframes'))
os.environ['KEYPOINT_FOLDER'] = os.environ.get('KEYPOINT_FOLDER', os.path.join(current_dir, 'keypoints'))
os.environ['UPLOAD_FOLDER'] = os.environ.get('UPLOAD_FOLDER', os.path.join(current_dir, 'uploads'))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('worker_service.log')
    ]
)
logger = logging.getLogger(__name__)

# Global shutdown event
shutdown_event = Event()

def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
    logger.info(f"Received signal {signum}, initiating shutdown...")
    shutdown_event.set()

def main():
    """Main Redis worker service function"""
    logger.info("=" * 60)
    logger.info("WinnerWay Redis Worker Service Starting")
    logger.info("=" * 60)
    
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    logger.info(f"Working directory: {os.getcwd()}")
    logger.info(f"MAX_WORKERS: {os.environ.get('MAX_WORKERS', 'not set')}")
    logger.info(f"REDIS_HOST: {os.environ.get('REDIS_HOST', 'not set')}")
    logger.info(f"REDIS_PORT: {os.environ.get('REDIS_PORT', 'not set')}")
    
    try:
        # Import t3 module and start Redis worker
        logger.info("Importing t3 module...")
        import t3
        
        # Test Redis connection
        redis_conn = t3.get_redis_connection()
        if not redis_conn:
            logger.error("❌ Cannot connect to Redis. Make sure Redis is running.")
            sys.exit(1)
        
        logger.info(f"✅ Connected to Redis at {os.environ.get('REDIS_HOST')}:{os.environ.get('REDIS_PORT')}")
        
        # Start Redis worker threads  
        logger.info("Starting Redis worker threads...")
        worker_thread, cleanup_thread = t3.start_redis_worker()
        
        logger.info("✅ Redis worker service initialized successfully")
        logger.info(f"🔄 Listening for jobs on Redis queue: {t3.REDIS_QUEUE_NAME}")
        logger.info("Press Ctrl+C to stop the service")
        
        # Keep the service running
        while not shutdown_event.is_set():
            try:
                # Check if threads are still alive and restart if needed
                if not worker_thread.is_alive():
                    logger.error("❌ Redis worker thread died, restarting...")
                    worker_thread = threading.Thread(target=t3.redis_worker_loop, daemon=True)
                    worker_thread.start()
                
                if not cleanup_thread.is_alive():
                    logger.warning("⚠️  Cleanup thread died, restarting...")
                    cleanup_thread = threading.Thread(target=t3.cleanup_old_jobs, daemon=True)
                    cleanup_thread.start()
                
                # Check queue status every 30 seconds
                try:
                    queue_size = redis_conn.llen(t3.REDIS_QUEUE_NAME)
                    if queue_size > 0:
                        logger.info(f"📊 {queue_size} job(s) in Redis queue")
                except Exception as e:
                    logger.warning(f"Failed to check queue size: {e}")
                
                # Sleep and check again
                shutdown_event.wait(30)  # Check every 30 seconds
                
            except KeyboardInterrupt:
                break
                
    except Exception as e:
        logger.error(f"❌ Failed to start Redis worker service: {e}")
        sys.exit(1)
    
    logger.info("🛑 Redis worker service shutting down...")
    logger.info("Worker threads will complete current jobs and then stop")

if __name__ == "__main__":
    main()