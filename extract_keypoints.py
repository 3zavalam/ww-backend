import os
import cv2
import json
import mediapipe as mp

mp_pose = mp.solutions.pose

def extract_keypoints_from_images(input_dir, output_dir):
    os.makedirs(output_dir, exist_ok=True)

    image_files = [f for f in os.listdir(input_dir) if f.endswith(".jpg")]
    total = len(image_files)
    success_count = 0
    failed = []

    for filename in image_files:
        image_path = os.path.join(input_dir, filename)
        output_path = os.path.join(output_dir, filename.replace(".jpg", ".json"))

        image = cv2.imread(image_path)
        if image is None:
            failed.append(filename)
            continue

        # Primero intentamos con modo rápido
        keypoints = None
        with mp_pose.Pose(static_image_mode=False) as pose:
            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            results = pose.process(rgb)
            if results.pose_landmarks and len(results.pose_landmarks.landmark) == 33:
                keypoints = results.pose_landmarks.landmark

        # Si falla, intentamos con modo estático
        if keypoints is None:
            with mp_pose.Pose(static_image_mode=True) as pose_static:
                rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
                results = pose_static.process(rgb)
                if results.pose_landmarks and len(results.pose_landmarks.landmark) == 33:
                    keypoints = results.pose_landmarks.landmark

        if keypoints is None:
            failed.append(filename)
            continue

        # Guardar keypoints en formato JSON
        data = [{"x": lm.x, "y": lm.y, "z": lm.z, "visibility": lm.visibility} for lm in keypoints]
        with open(output_path, 'w') as f:
            json.dump(data, f)

        success_count += 1

    print(f"✅ Keypoint extraction complete: {success_count}/{total} succeeded.")
    if failed:
        print(f"❌ Failed to extract keypoints from: {', '.join(failed)}")
