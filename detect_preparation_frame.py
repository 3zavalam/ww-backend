import cv2
import mediapipe as mp
import os
import math

mp_pose = mp.solutions.pose

def angle_between(p1, p2, p3):
    a = (p1[0] - p2[0], p1[1] - p2[1])
    b = (p3[0] - p2[0], p3[1] - p2[1])
    dot = a[0]*b[0] + a[1]*b[1]
    norm_a = (a[0]**2 + a[1]**2)**0.5
    norm_b = (b[0]**2 + b[1]**2)**0.5
    if norm_a == 0 or norm_b == 0:
        return 0
    return math.degrees(math.acos(max(min(dot / (norm_a * norm_b), 1.0), -1.0)))

def get_stroke_specific_parameters(stroke_type):
    """
    Devuelve parámetros específicos para cada tipo de golpe:
    - target_elbow_angle: ángulo ideal del codo en preparación
    - tolerance: tolerancia permitida
    - racket_position_weight: peso de la posición de la raqueta en el score
    - shoulder_rotation_weight: peso de la rotación del hombro
    """
    if stroke_type == "forehand":
        return {
            "target_elbow_angle": 110,
            "tolerance": 30,
            "racket_position_weight": 0.4,
            "shoulder_rotation_weight": 0.3,
            "search_region": (0.1, 0.6)  # 10%-60% del video
        }
    elif stroke_type == "backhand":
        return {
            "target_elbow_angle": 120,  # Backhand tiende a ser más extendido
            "tolerance": 25,
            "racket_position_weight": 0.5,
            "shoulder_rotation_weight": 0.4,
            "search_region": (0.1, 0.6)
        }
    elif stroke_type == "serve":
        return {
            "target_elbow_angle": 90,   # Serve tiene take-back más cerrado
            "tolerance": 35,
            "racket_position_weight": 0.3,
            "shoulder_rotation_weight": 0.5,  # Rotación más importante en serve
            "search_region": (0.05, 0.4)  # Preparación más temprana
        }
    else:
        # Default (forehand)
        return {
            "target_elbow_angle": 110,
            "tolerance": 30,
            "racket_position_weight": 0.4,
            "shoulder_rotation_weight": 0.3,
            "search_region": (0.1, 0.6)
        }

def calculate_shoulder_rotation_score(left_shoulder, right_shoulder, stroke_type, is_right_handed):
    """
    Calcula un score basado en la rotación de los hombros.
    Una buena preparación involucra rotación del torso.
    """
    # Calcular el ángulo de rotación de los hombros respecto horizontal
    dx = right_shoulder.x - left_shoulder.x
    dy = right_shoulder.y - left_shoulder.y
    shoulder_angle = math.degrees(math.atan2(dy, dx))
    
    # Normalizar a 0-180 grados
    shoulder_angle = abs(shoulder_angle)
    if shoulder_angle > 90:
        shoulder_angle = 180 - shoulder_angle
    
    if stroke_type == "forehand":
        if is_right_handed:
            # Forehand derecho: hombro derecho debe estar más atrás
            target_angle = 15  # Ligera rotación
        else:
            # Forehand zurdo: hombro izquierdo debe estar más atrás
            target_angle = 15
    elif stroke_type == "backhand":
        if is_right_handed:
            # Backhand derecho: hombro derecho debe estar más adelante
            target_angle = 25  # Mayor rotación
        else:
            # Backhand zurdo: hombro izquierdo debe estar más adelante
            target_angle = 25
    elif stroke_type == "serve":
        # Serve: rotación significativa (cuerpo de lado)
        target_angle = 30
    else:
        target_angle = 15
    
    # Calcular score basado en proximidad al ángulo objetivo
    angle_diff = abs(shoulder_angle - target_angle)
    score = max(0, 1 - (angle_diff / 45))  # Normalizar con máximo de 45°
    return score

def calculate_racket_height_score(wrist, shoulder, stroke_type):
    """
    Calcula score basado en la altura relativa de la raqueta en preparación
    """
    height_diff = wrist.y - shoulder.y  # Negativo = arriba del hombro
    
    if stroke_type == "forehand":
        # Forehand: raqueta ligeramente arriba o nivel del hombro
        target_height = -0.05  # Ligeramente arriba
        tolerance = 0.15
    elif stroke_type == "backhand":
        # Backhand: raqueta más a nivel del hombro
        target_height = 0.0    # Nivel del hombro
        tolerance = 0.12
    elif stroke_type == "serve":
        # Serve: raqueta claramente arriba del hombro
        target_height = -0.2   # Bien arriba
        tolerance = 0.2
    else:
        target_height = -0.05
        tolerance = 0.15
    
    distance = abs(height_diff - target_height)
    score = max(0, 1 - (distance / tolerance))
    return score

def detect_preparation_frame(video_path, output_path, is_right_handed=True, stroke_type="forehand"):
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise Exception(f"Cannot open video {video_path}")

    os.makedirs(output_path, exist_ok=True)
    
    # Obtener parámetros específicos del golpe
    params = get_stroke_specific_parameters(stroke_type)
    
    # Contar frames totales para delimitar región de búsqueda
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    search_start = int(total_frames * params["search_region"][0])
    search_end = int(total_frames * params["search_region"][1])
    
    pose = mp_pose.Pose(static_image_mode=False)
    
    best_score = -1  # Cambiamos a sistema de score alto = mejor
    best_frame = None
    best_frame_index = -1
    frame_index = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        # Solo analizar frames en la región de búsqueda específica del golpe
        if frame_index < search_start or frame_index > search_end:
            frame_index += 1
            continue

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = pose.process(rgb)

        if not results.pose_landmarks or len(results.pose_landmarks.landmark) < 33:
            frame_index += 1
            continue

        lm = results.pose_landmarks.landmark
        
        # Obtener puntos clave
        if is_right_handed:
            shoulder, elbow, wrist = lm[12], lm[14], lm[16]
        else:
            shoulder, elbow, wrist = lm[11], lm[13], lm[15]
        
        left_shoulder, right_shoulder = lm[11], lm[12]

        # Calcular métricas múltiples para score combinado
        p_shoulder = (shoulder.x, shoulder.y)
        p_elbow = (elbow.x, elbow.y)
        p_wrist = (wrist.x, wrist.y)

        # 1. Score del ángulo del codo (principal)
        elbow_angle = angle_between(p_shoulder, p_elbow, p_wrist)
        angle_diff = abs(elbow_angle - params["target_elbow_angle"])
        elbow_score = max(0, 1 - (angle_diff / params["tolerance"]))
        
        # 2. Score de rotación de hombros
        shoulder_score = calculate_shoulder_rotation_score(
            left_shoulder, right_shoulder, stroke_type, is_right_handed
        )
        
        # 3. Score de altura de la raqueta
        height_score = calculate_racket_height_score(wrist, shoulder, stroke_type)
        
        # Score combinado ponderado
        combined_score = (
            elbow_score * 0.5 +  # Ángulo del codo sigue siendo más importante
            shoulder_score * params["shoulder_rotation_weight"] +
            height_score * params["racket_position_weight"]
        )

        if combined_score > best_score:
            best_score = combined_score
            best_frame = frame.copy()
            best_frame_index = frame_index

        frame_index += 1

    cap.release()
    pose.close()

    # Umbral de calidad ajustable por tipo de golpe
    min_score_threshold = 0.4 if stroke_type == "serve" else 0.5
    
    if best_frame is not None and best_score >= min_score_threshold:
        out_path = os.path.join(output_path, "preparation.jpg")
        cv2.imwrite(out_path, best_frame)
        print(f"✅ Saved {stroke_type} preparation frame (score: {best_score:.2f}) at frame {best_frame_index} to {out_path}")
    else:
        print(f"❌ No valid {stroke_type} preparation frame found (best score: {best_score:.2f}, threshold: {min_score_threshold})")
