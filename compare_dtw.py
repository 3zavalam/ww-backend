import os
import json
import math
from fastdtw import fastdtw
from scipy.spatial.distance import euclidean

KEYPOINT_INDICES = [11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28]

def normalize_keypoints(keypoints):
    left_shoulder = keypoints[11]
    right_shoulder = keypoints[12]
    center_x = (left_shoulder["x"] + right_shoulder["x"]) / 2
    center_y = (left_shoulder["y"] + right_shoulder["y"]) / 2
    shoulder_dist = ((left_shoulder["x"] - right_shoulder["x"]) ** 2 +
                     (left_shoulder["y"] - right_shoulder["y"]) ** 2) ** 0.5
    scale = shoulder_dist if shoulder_dist > 0 else 1.0
    return {
        i: ((kp["x"] - center_x) / scale, (kp["y"] - center_y) / scale)
        for i, kp in keypoints.items()
    }

def load_keypoints(json_path):
    with open(json_path, 'r') as f:
        raw = json.load(f)
    kp_dict = {i: kp for i, kp in enumerate(raw)}
    return normalize_keypoints(kp_dict)

def angle_between(p1, p2, p3):
    a = (p1[0] - p2[0], p1[1] - p2[1])
    b = (p3[0] - p2[0], p3[1] - p2[1])
    dot = a[0]*b[0] + a[1]*b[1]
    norm_a = math.hypot(*a)
    norm_b = math.hypot(*b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    cos_theta = max(min(dot / (norm_a * norm_b), 1.0), -1.0)
    return math.degrees(math.acos(cos_theta))

def compare_frames(user_path, reference_path, frame_name):
    user_file = os.path.join(user_path, f"{frame_name}.json")
    ref_file = os.path.join(reference_path, f"{frame_name}.json")

    if not os.path.exists(user_file):
        if frame_name == "impact":
            return f"{frame_name.capitalize()}: ⚠️ Could not detect your impact frame. Please try uploading a different video with clearer contact point."
        return f"{frame_name.capitalize()}: Data missing (user)."

    if not os.path.exists(ref_file):
        return f"{frame_name.capitalize()}: Data missing (reference)."

    try:
        user_kps = load_keypoints(user_file)
        ref_kps = load_keypoints(ref_file)
    except Exception as e:
        return f"{frame_name.capitalize()}: Failed to load keypoints ({e})"

    user_vec = [user_kps[i] for i in KEYPOINT_INDICES if i in user_kps]
    ref_vec = [ref_kps[i] for i in KEYPOINT_INDICES if i in ref_kps]

    if not user_vec or not ref_vec:
        return f"{frame_name.capitalize()}: Insufficient keypoints for DTW."

    distance, _ = fastdtw(user_vec, ref_vec, dist=euclidean)
    angle_feedback = []

    if all(i in user_kps for i in [11, 13, 15]) and all(i in ref_kps for i in [11, 13, 15]):
        diff_L = abs(angle_between(user_kps[11], user_kps[13], user_kps[15]) -
                     angle_between(ref_kps[11], ref_kps[13], ref_kps[15]))
        if diff_L > 15:
            angle_feedback.append(f"Left elbow angle differs by {diff_L:.1f}°.")

    if all(i in user_kps for i in [12, 14, 16]) and all(i in ref_kps for i in [12, 14, 16]):
        diff_R = abs(angle_between(user_kps[12], user_kps[14], user_kps[16]) -
                     angle_between(ref_kps[12], ref_kps[14], ref_kps[16]))
        if diff_R > 15:
            angle_feedback.append(f"Right elbow angle differs by {diff_R:.1f}°.")

    if distance < 20:
        base_feedback = f"{frame_name.capitalize()}: 🟢 Excellent similarity (DTW={distance:.1f}). "
    elif distance < 65:
        base_feedback = f"{frame_name.capitalize()}: 🟡 Moderate difference (DTW={distance:.1f}). "
    else:
        base_feedback = f"{frame_name.capitalize()}: 🔴 High difference (DTW={distance:.1f}). "

    return base_feedback + " ".join(angle_feedback)

def compare_all(user_folder, stroke_type):
    reference_base = "reference_keypoints"
    best_match = {
        "player": None,
        "clip": None,
        "distance": float('inf'),
        "feedback": "No reference videos found for comparison."
    }

    reference_players_path = os.path.join(reference_base)
    if not os.path.isdir(reference_players_path):
        return { "feedback": f"Reference directory not found at {reference_players_path}" }

    for player_name in os.listdir(reference_players_path):
        player_path = os.path.join(reference_players_path, player_name, stroke_type)
        if not os.path.isdir(player_path):
            continue

        total_distance = 0
        feedback_parts = []
        valid_comparison = True

        for frame in ["preparation", "impact", "follow_through"]:
            user_frame_file = os.path.join(user_folder, f"{frame}.json")
            ref_frame_file = os.path.join(player_path, f"{frame}.json")

            if not os.path.exists(user_frame_file) or not os.path.exists(ref_frame_file):
                valid_comparison = False
                break
            
            user_kps = load_keypoints(user_frame_file)
            ref_kps = load_keypoints(ref_frame_file)
            
            user_vec = [user_kps[i] for i in KEYPOINT_INDICES if i in user_kps]
            ref_vec = [ref_kps[i] for i in KEYPOINT_INDICES if i in ref_kps]

            if not user_vec or not ref_vec:
                valid_comparison = False
                break
                
            distance, _ = fastdtw(user_vec, ref_vec, dist=euclidean)
            total_distance += distance
            feedback_parts.append(compare_frames(user_folder, player_path, frame))

        if valid_comparison and total_distance < best_match["distance"]:
            best_match["distance"] = total_distance
            best_match["player"] = player_name
            best_match["feedback"] = "\n".join(feedback_parts)
            
            # Find the corresponding video clip
            video_folder = os.path.join("data", stroke_type)
            found_clip = False
            if os.path.isdir(video_folder):
                for file in os.listdir(video_folder):
                    # Simplistic check, might need refinement
                    if player_name.split('_')[0] in file and file.endswith(".mp4"):
                        best_match["clip"] = file
                        found_clip = True
                        break
            if not found_clip:
                best_match["clip"] = None


    return {
        "feedback": best_match["feedback"],
        "reference_clip": best_match["clip"]
    }