import os, json, shutil, subprocess, redis
from uuid import uuid4
import boto3

from extract_rally_shot import extract_one_shot_from_rally
from detect_impact_frame import detect_impact_frame
from detect_preparation_frame import detect_preparation_frame
from extract_follow_through import extract_follow_through
from extract_keypoints import extract_keypoints_from_images
from compare_dtw import compare_all
from generate_pose_overlay import generate_pose_overlay
from analyze_with_ai import build_stroke_json, analyze_stroke_with_ai, generate_drills_with_ai

# ── env ──
REDIS_HOST        = os.getenv("REDIS_HOST", "t2.micro.internal")
REDIS_PORT        = int(os.getenv("REDIS_PORT", 6379))
AWS_REGION        = os.getenv("AWS_REGION", "us-east-1")
STATIC_FOLDER     = os.getenv("STATIC_FOLDER", "/mnt/ww/static")
KEYFRAME_FOLDER   = os.getenv("KEYFRAME_FOLDER", "/mnt/ww/keyframes")
KEYPOINT_FOLDER   = os.getenv("KEYPOINT_FOLDER", "/mnt/ww/keypoints")
DOWNLOAD_FOLDER   = os.getenv("DOWNLOAD_FOLDER", "/mnt/ww/tmp")
CLIP_FOLDER       = os.path.join(STATIC_FOLDER, "clips")
PUBLIC_KF_FOLDER  = os.path.join(STATIC_FOLDER, "keyframes")

for p in [KEYFRAME_FOLDER, KEYPOINT_FOLDER, CLIP_FOLDER, PUBLIC_KF_FOLDER, DOWNLOAD_FOLDER]:
    os.makedirs(p, exist_ok=True)

r   = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True)
s3  = boto3.client("s3", region_name=AWS_REGION)

def safe_rm(path):
    try:
        shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
    except Exception:
        pass

def download_from_s3(s3_uri, local_path):
    # s3://bucket-name/path/to/file.mp4
    bucket, key = s3_uri.replace("s3://", "").split("/", 1)
    s3.download_file(bucket, key, local_path)

while True:
    raw = r.brpop("ww:jobs", timeout=30)
    if not raw:
        continue

    job = json.loads(raw[1])
    job_id   = job["id"]
    s3_path  = job["s3_path"]
    email    = job["email"]
    stroke   = job["stroke_type"]
    handed   = job["handedness"]

    r.hset("ww:status", job_id, "processing")

    uid   = uuid4().hex
    base  = os.path.splitext(os.path.basename(s3_path))[0].replace(".", "_")
    video_name = f"{base}_{uid}"
    local_video_path = os.path.join(DOWNLOAD_FOLDER, f"{video_name}.mp4")

    try:
        # 0) Download video from S3
        download_from_s3(s3_path, local_video_path)

        # 1) overlay
        overlay_temp = os.path.join(CLIP_FOLDER, f"{video_name}_overlay.avi")
        final_clip   = os.path.join(CLIP_FOLDER, f"{video_name}.mp4")
        generate_pose_overlay(local_video_path, overlay_temp)
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

        # 2) keyframes
        kf_output = os.path.join(KEYFRAME_FOLDER, video_name)
        os.makedirs(kf_output, exist_ok=True)
        is_right = handed == "right"
        detect_preparation_frame(local_video_path, kf_output, is_right_handed=is_right)
        detect_impact_frame(local_video_path, kf_output, is_right_handed=is_right)
        extract_follow_through(local_video_path, kf_output)

        # 3) keypoints
        kp_output = os.path.join(KEYPOINT_FOLDER, video_name)
        extract_keypoints_from_images(kf_output, kp_output)

        # 4) AI feedback
        comparison   = compare_all(kp_output, stroke)
        stroke_json  = build_stroke_json(kp_output)
        issues       = analyze_stroke_with_ai(stroke_json, stroke)
        drills       = generate_drills_with_ai(issues, stroke)

        # 5) ref clip copy (opcional)
        reference_url = None
        ref_clip = comparison.get("reference_clip")
        if ref_clip:
            try:
                shutil.copy(os.path.join("data", stroke, ref_clip),
                            os.path.join(CLIP_FOLDER, ref_clip))
                reference_url = f"/static/clips/{ref_clip}"
            except Exception:
                pass

        # 6) mover keyframes a carpeta pública
        public_kf = os.path.join(PUBLIC_KF_FOLDER, video_name)
        os.makedirs(public_kf, exist_ok=True)
        kf_urls = {}
        for phase in ["preparation", "impact", "follow_through"]:
            src = os.path.join(kf_output, f"{phase}.jpg")
            if os.path.exists(src):
                dst = os.path.join(public_kf, f"{phase}.jpg")
                shutil.copy(src, dst)
                kf_urls[phase] = f"/static/keyframes/{video_name}/{phase}.jpg"

        # 7) resultado a Redis
        r.hset("ww:results", job_id, json.dumps({
            "feedback":      issues,
            "drills":        drills,
            "video_url":     f"/static/clips/{video_name}.mp4",
            "keyframes":     kf_urls,
            "reference_url": reference_url
        }))
        r.hset("ww:status", job_id, "done")

    except Exception as e:
        r.hset("ww:status", job_id, "error")
        r.hset("ww:results", job_id, json.dumps({"error": str(e)}))

    finally:
        safe_rm(kf_output)
        safe_rm(kp_output)
        safe_rm(local_video_path)