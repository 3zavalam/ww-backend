import cv2
import os

def extract_keyframes(video_path, output_dir):
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise Exception("Could not open video")

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    # Solo extraemos preparación (20%) y follow-through (80%)
    frame_indices = [
        int(total_frames * 0.2),
        int(total_frames * 0.8),
    ]
    frame_labels = ["preparation", "follow_through"]

    os.makedirs(output_dir, exist_ok=True)

    extracted = 0
    current_frame = 0

    while extracted < 2:
        ret, frame = cap.read()
        if not ret:
            break

        if current_frame == frame_indices[extracted]:
            label = frame_labels[extracted]
            filename = os.path.join(output_dir, f"{label}.jpg")
            cv2.imwrite(filename, frame)
            print(f"Saved {label} frame to {filename}")
            extracted += 1

        current_frame += 1

    cap.release()
