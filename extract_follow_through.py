import cv2
import mediapipe as mp
import os
import math
import numpy as np

mp_pose = mp.solutions.pose

def angle_between(p1, p2, p3):
    """Calcula el ángulo entre tres puntos"""
    a = (p1[0] - p2[0], p1[1] - p2[1])
    b = (p3[0] - p2[0], p3[1] - p2[1])
    dot = a[0]*b[0] + a[1]*b[1]
    norm_a = math.hypot(*a)
    norm_b = math.hypot(*b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    cos_theta = max(min(dot / (norm_a * norm_b), 1.0), -1.0)
    return math.degrees(math.acos(cos_theta))

def calculate_arm_extension_ratio(shoulder, elbow, wrist):
    """Calcula el ratio de extensión del brazo (0=completamente flexionado, 1=completamente extendido)"""
    elbow_angle = angle_between((shoulder.x, shoulder.y), (elbow.x, elbow.y), (wrist.x, wrist.y))
    # Normalizar: 60° = completamente flexionado (0), 180° = completamente extendido (1)
    extension_ratio = max(0, min(1, (elbow_angle - 60) / 120))
    return extension_ratio

def calculate_racket_position_score(wrist, shoulder, stroke_type):
    """
    Calcula un score basado en la posición final esperada de la raqueta
    según el tipo de golpe
    """
    # Posición relativa de la muñeca respecto al hombro
    dx = wrist.x - shoulder.x
    dy = wrist.y - shoulder.y
    
    if stroke_type == "forehand":
        # Forehand: raqueta debe terminar alta y cruzada hacia el lado opuesto
        target_x = -0.3  # Cruzada hacia el lado izquierdo
        target_y = -0.2  # Ligeramente arriba del hombro
    elif stroke_type == "backhand":
        # Backhand: raqueta termina más extendida hacia adelante
        target_x = 0.4   # Hacia adelante del lado dominante
        target_y = -0.1  # Nivel del hombro o ligeramente arriba
    elif stroke_type == "serve":
        # Serve: raqueta termina bajando hacia el lado opuesto
        target_x = -0.4  # Hacia el lado opuesto
        target_y = 0.3   # Abajo del hombro
    else:
        # Default: posición neutral
        target_x = 0.0
        target_y = 0.0
    
    # Calcular distancia a la posición objetivo
    distance = math.hypot(dx - target_x, dy - target_y)
    # Convertir a score (menor distancia = mayor score)
    score = max(0, 1 - distance)
    return score

def detect_movement_stabilization(positions_history, threshold=0.02, min_stable_frames=5):
    """
    Detecta cuando el movimiento se estabiliza (velocidad baja y consistente)
    """
    if len(positions_history) < min_stable_frames + 1:
        return False
    
    # Calcular velocidades de los últimos frames
    recent_velocities = []
    for i in range(-min_stable_frames, 0):
        if i == 0:
            break
        curr_pos = positions_history[i]
        prev_pos = positions_history[i-1]
        velocity = math.hypot(curr_pos[0] - prev_pos[0], curr_pos[1] - prev_pos[1])
        recent_velocities.append(velocity)
    
    # Verificar si todas las velocidades están por debajo del umbral
    return all(v < threshold for v in recent_velocities)

def extract_follow_through(video_path, output_dir, is_right_handed=True, stroke_type="forehand"):
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise Exception("Could not open video")

    os.makedirs(output_dir, exist_ok=True)
    
    pose = mp_pose.Pose(static_image_mode=False)
    frames = []
    follow_through_scores = []
    wrist_positions = []
    
    frame_index = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break

        # Si el jugador es zurdo, espejamos el frame
        if not is_right_handed:
            frame = cv2.flip(frame, 1)

        frames.append(frame)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = pose.process(rgb)

        if not results.pose_landmarks:
            follow_through_scores.append(0)
            wrist_positions.append(None)
            frame_index += 1
            continue

        lm = results.pose_landmarks.landmark
        
        # Seleccionar puntos según handedness
        if is_right_handed:
            shoulder, elbow, wrist = lm[12], lm[14], lm[16]
        else:
            shoulder, elbow, wrist = lm[11], lm[13], lm[15]

        # Guardar posición de muñeca para análisis de estabilización
        wrist_positions.append((wrist.x, wrist.y))
        
        # Calcular métricas de follow-through
        extension_ratio = calculate_arm_extension_ratio(shoulder, elbow, wrist)
        position_score = calculate_racket_position_score(wrist, shoulder, stroke_type)
        
        # Score combinado: extensión + posición correcta
        combined_score = (extension_ratio * 0.6) + (position_score * 0.4)
        follow_through_scores.append(combined_score)
        
        frame_index += 1

    cap.release()
    pose.close()
    
    # Encontrar el mejor frame de follow-through
    best_frame_idx = _find_best_follow_through_frame(
        follow_through_scores, wrist_positions, frames, stroke_type
    )
    
    if best_frame_idx >= 0 and best_frame_idx < len(frames):
        filename = os.path.join(output_dir, "follow_through.jpg")
        cv2.imwrite(filename, frames[best_frame_idx])
        print(f"✅ Saved follow_through frame (biomechanical detection) at index {best_frame_idx} to {filename}")
    else:
        # Fallback al método temporal original
        print("🔄 Fallback a método temporal...")
        _extract_follow_through_temporal_fallback(video_path, output_dir)

def _find_best_follow_through_frame(scores, wrist_positions, frames, stroke_type):
    """
    Encuentra el mejor frame de follow-through basado en:
    1. Score biomecánico alto
    2. Estabilización del movimiento
    3. Posición en la segunda mitad del video
    """
    if len(scores) < 10:
        return len(frames) // 2
    
    # Buscar solo en la segunda mitad del video
    start_search = len(scores) // 2
    search_scores = scores[start_search:]
    search_positions = wrist_positions[start_search:]
    
    # Encontrar candidatos con score alto
    threshold = max(search_scores) * 0.7  # 70% del score máximo
    candidates = []
    
    for i, score in enumerate(search_scores):
        if score >= threshold:
            # Verificar estabilización del movimiento
            global_idx = start_search + i
            if global_idx >= 5:  # Necesitamos historia previa
                recent_positions = [pos for pos in wrist_positions[global_idx-5:global_idx+1] if pos is not None]
                if len(recent_positions) >= 5:
                    is_stable = detect_movement_stabilization(recent_positions)
                    if is_stable:
                        candidates.append((global_idx, score))
    
    # Si encontramos candidatos estables, tomar el de mayor score
    if candidates:
        best_candidate = max(candidates, key=lambda x: x[1])
        return best_candidate[0]
    
    # Si no hay candidatos estables, tomar el de mayor score en la búsqueda
    if search_scores:
        max_score_idx = search_scores.index(max(search_scores))
        return start_search + max_score_idx
    
    # Fallback final
    return len(frames) * 3 // 4  # 75% del video

def _extract_follow_through_temporal_fallback(video_path, output_dir):
    """Método fallback usando heurística temporal (método original)"""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return
    
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    follow_index = int(total_frames * 0.8)  # 80%
    
    current_frame = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if current_frame == follow_index:
            filename = os.path.join(output_dir, "follow_through.jpg")
            cv2.imwrite(filename, frame)
            print(f"✅ Saved follow_through frame (temporal fallback) to {filename}")
            break

        current_frame += 1

    cap.release()
