import os
import uuid
import shutil
from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_from_directory, url_for, current_app
from flask_cors import CORS
from werkzeug.utils import secure_filename

from extract_rally_shot import extract_one_shot_from_rally
from detect_impact_frame import detect_impact_frame
from detect_preparation_frame import detect_preparation_frame
from extract_follow_through import extract_follow_through
from extract_keypoints import extract_keypoints_from_images
from compare_dtw import compare_all
from generate_pose_overlay import generate_pose_overlay
from analyze_with_ai import build_stroke_json, analyze_stroke_with_ai, generate_drills_with_ai, client

from routes.stripe import stripe_bp 
from routes.verify import verify_bp
from routes.webhook import webhook_bp

# Obtener orígenes permitidos desde variable de entorno o usar valor por defecto
allowed_origins_env = os.getenv('ALLOWED_ORIGINS')
if allowed_origins_env:
    allowed_origins = allowed_origins_env.split(',')
else:
    # Valor por defecto para desarrollo local
    allowed_origins = ["https://www.winnerway.pro", "http://localhost:8080"]

app = Flask(__name__)
CORS(app, origins=allowed_origins)
app.register_blueprint(stripe_bp)
app.register_blueprint(verify_bp)
app.register_blueprint(webhook_bp)


# Carpetas de trabajo
UPLOAD_FOLDER = "uploads"
KEYFRAME_FOLDER = "keyframes"
KEYPOINT_FOLDER = "keypoints"
CLIP_FOLDER = os.path.join("static", "clips")
PUBLIC_KEYFRAME_FOLDER = os.path.join("static", "keyframes")

# Crear directorios si no existen
for path in [UPLOAD_FOLDER, KEYFRAME_FOLDER, KEYPOINT_FOLDER, CLIP_FOLDER, PUBLIC_KEYFRAME_FOLDER]:
    os.makedirs(path, exist_ok=True)


@app.route("/upload", methods=['POST', 'OPTIONS'])
def upload_video():
    # Manejo de preflight
    if request.method == 'OPTIONS':
        return '', 204

    # 1) Validación básica
    video = request.files.get("video")
    email = request.form.get("email")
    stroke_type = request.form.get("stroke_type", "forehand")
    if not video or not email:
        return jsonify({"error": "Falta video o email"}), 400

    # 2) Guardar el video original
    filename = secure_filename(video.filename)
    uid = uuid.uuid4().hex
    base, ext = os.path.splitext(filename)
    video_name = f"{base}_{uid}"
    original_path = os.path.join(UPLOAD_FOLDER, video_name + ext)
    video.save(original_path)

    # 3) Extraer un solo rally shot (desactivado por ahora)
    # Usar directamente el original por ahora
    extracted_clip = original_path

    # 4) Generar overlay de pose
    overlay_temp = os.path.join(CLIP_FOLDER, f"{video_name}_overlay.avi")
    final_clip   = os.path.join(CLIP_FOLDER, f"{video_name}.mp4")
    try:
        generate_pose_overlay(extracted_clip, overlay_temp)
        if os.path.exists(overlay_temp):
            # Convertir AVI a MP4 con ffmpeg
            import subprocess
            result = subprocess.run([
                'ffmpeg', '-i', overlay_temp, '-c:v', 'libx264', '-preset', 'fast', 
                '-crf', '23', '-c:a', 'aac', '-y', final_clip
            ], capture_output=True, text=True)
            
            if result.returncode == 0:
                os.remove(overlay_temp)
            else:
                # Si falla FFmpeg, usar AVI como fallback
                shutil.move(overlay_temp, final_clip)
        else:
            shutil.copy(extracted_clip, final_clip)
    except Exception:
        current_app.logger.exception("Error overlay")
        return jsonify({"error": "Fallo al generar overlay"}), 500

    # 5) Extraer keyframes
    kf_output = os.path.join(KEYFRAME_FOLDER, video_name)
    os.makedirs(kf_output, exist_ok=True)

    handedness_str = request.form.get("handedness", "right")  
    is_right_handed = (handedness_str == "right")

    try:
        detect_preparation_frame(extracted_clip, kf_output, is_right_handed=is_right_handed)
        detect_impact_frame(extracted_clip, kf_output, is_right_handed=is_right_handed)
        extract_follow_through(extracted_clip, kf_output)
    except Exception:
        current_app.logger.exception("Error keyframes")
        return jsonify({"error": "No se pudieron extraer fotogramas"}), 500

    # 6) Copiar keyframes a carpeta pública y generar URLs
    public_kf = os.path.join(PUBLIC_KEYFRAME_FOLDER, video_name)
    os.makedirs(public_kf, exist_ok=True)
    keyframe_urls = {}
    for phase in ["preparation", "impact", "follow_through"]:
        src = os.path.join(kf_output, f"{phase}.jpg")
        if os.path.exists(src):
            dst = os.path.join(public_kf, f"{phase}.jpg")
            shutil.copy(src, dst)
            keyframe_urls[phase] = url_for(
                'static', filename=f"keyframes/{video_name}/{phase}.jpg", _external=True, _scheme='https'
            )

    # 7) Extraer keypoints
    kp_output = os.path.join(KEYPOINT_FOLDER, video_name)
    try:
        extract_keypoints_from_images(kf_output, kp_output)
    except FileNotFoundError:
        return jsonify({"error": "No hay keyframes válidos"}), 400
    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception:
        current_app.logger.exception("Error keypoints")
        return jsonify({"error": "Error procesando video"}), 500

    # 8) Análisis y drills con AI
    try:
        comparison = compare_all(kp_output, stroke_type)
        stroke_json = build_stroke_json(kp_output)

        if email.lower() == "password":
            issues, drills = ["🧪 Modo test…"], []
        else:
            issues = analyze_stroke_with_ai(stroke_json, stroke_type)
            drills = generate_drills_with_ai(issues, stroke_type)
    except Exception:
        current_app.logger.exception("Error feedback")
        return jsonify({"error": "No se pudo generar feedback"}), 500

    # 9) Copiar clip de referencia y generar su URL
    reference_url = None
    ref_clip = comparison.get("reference_clip")
    if not ref_clip:
        data_dir = os.path.join("data", stroke_type)
        if os.path.isdir(data_dir):
            candidates = [f for f in os.listdir(data_dir) if f.endswith(".mp4")]
            if candidates:
                ref_clip = candidates[0]
    if ref_clip:
        try:
            shutil.copy(os.path.join("data", stroke_type, ref_clip),
                        os.path.join(CLIP_FOLDER, ref_clip))
            reference_url = url_for(
                'static', filename=f"clips/{ref_clip}", _external=True
            )
        except Exception:
            current_app.logger.warning(f"No se pudo copiar referencia {ref_clip}")

    # 10) Limpieza de archivos temporales
    def safe_remove(path):
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
        except Exception:
            pass

    safe_remove(original_path)
    if extracted_clip != original_path:
        safe_remove(extracted_clip)
    safe_remove(kf_output)
    safe_remove(kp_output)

    # 11) Respuesta final
    return jsonify({
        "feedback":      issues,
        "drills":        drills,
        "video_url":     url_for('static', filename=f"clips/{video_name}.mp4", _external=True),
        "keyframes":     keyframe_urls,
        "reference_url": reference_url
    }), 200


@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory("static", filename)


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5050)