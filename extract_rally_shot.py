import cv2
import mediapipe as mp

mp_pose = mp.solutions.pose

def calculate_angle(a, b, c):
    import math
    ab = (a[0] - b[0], a[1] - b[1])
    cb = (c[0] - b[0], c[1] - b[1])

    dot = ab[0] * cb[0] + ab[1] * cb[1]
    norm_ab = (ab[0]**2 + ab[1]**2) ** 0.5
    norm_cb = (cb[0]**2 + cb[1]**2) ** 0.5

    if norm_ab == 0 or norm_cb == 0:
        return 0

    cos_theta = max(min(dot / (norm_ab * norm_cb), 1.0), -1.0)
    return math.degrees(math.acos(cos_theta))

def extract_one_shot_from_rally(video_path, output_path, is_right_handed=True):
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise Exception(f"Cannot open video {video_path}")

    pose = mp_pose.Pose(static_image_mode=False)

    frame_buffer = []
    preparation_index = None
    impact_index = None
    follow_through_index = None

    max_extension = -1
    impact_candidate_index = -1

    frame_index = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = pose.process(rgb)

        frame_buffer.append(frame)

        if results.pose_landmarks:
            lm = results.pose_landmarks.landmark

            shoulder = lm[12] if is_right_handed else lm[11]
            elbow = lm[14] if is_right_handed else lm[13]
            wrist = lm[16] if is_right_handed else lm[15]

            angle = calculate_angle((shoulder.x, shoulder.y), (elbow.x, elbow.y), (wrist.x, wrist.y))

            if preparation_index is None and 60 < angle < 110:
                preparation_index = frame_index

            shoulder_to_wrist_dist = ((shoulder.x - wrist.x) ** 2 + (shoulder.y - wrist.y) ** 2) ** 0.5
            if shoulder_to_wrist_dist > max_extension:
                max_extension = shoulder_to_wrist_dist
                impact_candidate_index = frame_index

            if preparation_index and impact_candidate_index and frame_index > impact_candidate_index:
                if angle < 80:
                    follow_through_index = frame_index
                    break

        frame_index += 1

    cap.release()
    pose.close()

    if preparation_index is not None and follow_through_index is not None:
        out = cv2.VideoWriter(
            output_path,
            cv2.VideoWriter_fourcc(*"mp4v"),
            cap.get(cv2.CAP_PROP_FPS),
            (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
        )

        for f in frame_buffer[preparation_index:follow_through_index + 1]:
            out.write(f)
        out.release()
        print(f"Saved extracted shot to {output_path}")
    else:
        print("Failed to identify full stroke. No video saved.")