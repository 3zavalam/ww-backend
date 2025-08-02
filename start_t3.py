#!/usr/bin/env python3
"""
Simple startup script for t3.py
Run this instead of the shell script if you prefer direct Python execution
"""
import os
import sys

# Ensure we're in the right directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Set environment variables for single worker configuration
os.environ['MAX_WORKERS'] = '1'
os.environ['REDIS_HOST'] = 'localhost'
os.environ['REDIS_PORT'] = '6379'

# Set local paths
current_dir = os.getcwd()
os.environ['STATIC_FOLDER'] = os.path.join(current_dir, 'static')
os.environ['KEYFRAME_FOLDER'] = os.path.join(current_dir, 'temp_keyframes')
os.environ['KEYPOINT_FOLDER'] = os.path.join(current_dir, 'keypoints')
os.environ['UPLOAD_FOLDER'] = os.path.join(current_dir, 'uploads')

print("Starting t3.py with single worker configuration...")
print(f"Working directory: {current_dir}")
print(f"MAX_WORKERS: {os.environ['MAX_WORKERS']}")

# Import and run the application
if __name__ == "__main__":
    import t3
    # The app.run() call is already in t3.py's __main__ block