import os
from extract_keyframes import extract_keyframes
from extract_keypoints import extract_keypoints_from_images

VIDEO_BASE_DIR = "data"
OUTPUT_DIR = "reference_keypoints"

for stroke_type in ["forehand", "backhand", "serve"]:
    stroke_path = os.path.join(VIDEO_BASE_DIR, stroke_type)
    for filename in os.listdir(stroke_path):
        if not filename.endswith(".mp4"):
            continue

        name = os.path.splitext(filename)[0]
        player_name = "_".join(name.split("_")[:2])  # ej. roger_federer
        video_path = os.path.join(stroke_path, filename)

        print(f"Processing {video_path}...")

        # Ruta de output para keyframes y keypoints
        keyframe_path = os.path.join("temp_keyframes", name)
        keypoint_path = os.path.join(OUTPUT_DIR, player_name, stroke_type)

        extract_keyframes(video_path, keyframe_path)
        extract_keypoints_from_images(keyframe_path, keypoint_path)

        print(f"→ Saved to {keypoint_path}\n")
