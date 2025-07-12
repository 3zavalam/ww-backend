import os
import shutil
import tempfile

from detect_preparation_frame import detect_preparation_frame
from detect_impact_frame import detect_impact_frame
from extract_follow_through import extract_follow_through
from extract_keypoints import extract_keypoints_from_images
from compare_dtw import compare_all

def test_single_video(video_path, stroke_type, handedness="right"):
    """
    Runs the full comparison pipeline for a single local video file.
    Assumes this script is run from the 'backend/web-beta' directory.
    """
    if not os.path.exists(video_path):
        print(f"Error: Video file not found at {video_path}")
        print(f"Current working directory: {os.getcwd()}")
        return

    # Create temporary directories for keyframes and keypoints
    temp_dir = tempfile.mkdtemp(prefix="ww_test_")
    keyframe_output_dir = os.path.join(temp_dir, "keyframes")
    keypoint_output_dir = os.path.join(temp_dir, "keypoints")
    os.makedirs(keyframe_output_dir, exist_ok=True)
    os.makedirs(keypoint_output_dir, exist_ok=True)

    print(f"Processing video: {video_path}")

    try:
        # 1. Extract keyframes
        print("Extracting keyframes (preparation, impact, follow-through)...")
        is_right = handedness == "right"
        detect_preparation_frame(video_path, keyframe_output_dir, is_right_handed=is_right)
        detect_impact_frame(video_path, keyframe_output_dir, is_right_handed=is_right)
        extract_follow_through(video_path, keyframe_output_dir)
        print("Keyframe extraction complete.")

        # 2. Extract keypoints from keyframes
        print("Extracting keypoints...")
        extract_keypoints_from_images(keyframe_output_dir, keypoint_output_dir)
        print("Keypoint extraction complete.")

        # 3. Run comparison
        print("Running comparison...")
        result = compare_all(keypoint_output_dir, stroke_type)
        print("\n--- Comparison Result ---")
        print(f"Feedback:\n{result['feedback']}")
        print(f"Best match reference clip: {result['reference_clip']}")
        print("-------------------------\n")

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"\nAn error occurred: {e}")
    finally:
        # Clean up temporary directories
        print(f"Cleaning up temporary directory: {temp_dir}")
        shutil.rmtree(temp_dir)

if __name__ == "__main__":
    # --- Configuration ---
    # INSTRUCTIONS:
    # 1. Activate your virtual environment (e.g., `source ../../.venv/bin/activate` if you have one in root)
    # 2. `cd` into the `backend/web-beta` directory.
    # 3. Run this script: `python3 test_comparison.py`
    #
    # You can change the video file to test below.
    # The path should be relative to the `backend/web-beta` directory.
    
    # Let's use one of the new videos for the test.
    video_to_test = "data/forehand/carlos_alcaraz_fh_ts_04.mp4"
    stroke_of_video = "forehand" # "forehand", "backhand", or "serve"
    handedness_of_player = "right"
    # ---------------------

    test_single_video(video_to_test, stroke_of_video, handedness_of_player) 