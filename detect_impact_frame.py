import cv2
import mediapipe as mp
import os
import math
import numpy as np

mp_pose = mp.solutions.pose

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

def calculate_angular_velocity(angles, frame_rate=30.0):
    """Calcula la velocidad angular entre frames consecutivos"""
    if len(angles) < 2:
        return []
    
    velocities = []
    for i in range(1, len(angles)):
        angle_diff = angles[i] - angles[i-1]
        # Normalizar diferencia angular (-180 a 180)
        if angle_diff > 180:
            angle_diff -= 360
        elif angle_diff < -180:
            angle_diff += 360
        
        velocity = angle_diff * frame_rate  # grados/segundo
        velocities.append(velocity)
    
    return velocities

def smooth_signal(signal, window_size=5):
    """Suaviza la señal usando media móvil"""
    if len(signal) < window_size:
        return signal
    
    smoothed = []
    for i in range(len(signal)):
        start = max(0, i - window_size // 2)
        end = min(len(signal), i + window_size // 2 + 1)
        smoothed.append(np.mean(signal[start:end]))
    
    return smoothed

def detect_impact_frame(video_path, output_path, is_right_handed=True, stroke_type="forehand"):
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise Exception(f"Cannot open video {video_path}")

    os.makedirs(output_path, exist_ok=True)
    
    # Obtener frame rate del video
    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0:
        fps = 30.0  # fallback

    pose = mp_pose.Pose(static_image_mode=False)
    frames = []
    elbow_angles = []
    wrist_shoulder_distances = []
    forearm_angles = []  # Ángulo del antebrazo respecto horizontal
    
    while True:
        ret, frame = cap.read()
        if not ret:
            break

        # Si el jugador es zurdo, espejamos el frame para usar siempre la misma referencia
        if not is_right_handed:
            frame = cv2.flip(frame, 1)

        frames.append(frame)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = pose.process(rgb)

        if not results.pose_landmarks:
            elbow_angles.append(None)
            wrist_shoulder_distances.append(None)
            forearm_angles.append(None)
            continue

        lm = results.pose_landmarks.landmark
        # Selección de puntos según mano dominante
        if is_right_handed:
            shoulder, elbow, wrist = lm[12], lm[14], lm[16]
        else:
            shoulder, elbow, wrist = lm[11], lm[13], lm[15]

        # Calcular métricas biomecánicas
        p_shoulder = (shoulder.x, shoulder.y)
        p_elbow = (elbow.x, elbow.y)
        p_wrist = (wrist.x, wrist.y)
        
        # 1. Ángulo del codo
        elbow_angle = angle_between(p_shoulder, p_elbow, p_wrist)
        elbow_angles.append(elbow_angle)
        
        # 2. Distancia hombro-muñeca (extensión)
        dist = math.hypot(wrist.x - shoulder.x, wrist.y - shoulder.y)
        wrist_shoulder_distances.append(dist)
        
        # 3. Ángulo del antebrazo respecto horizontal
        forearm_angle = math.degrees(math.atan2(wrist.y - elbow.y, wrist.x - elbow.x))
        forearm_angles.append(forearm_angle)

    cap.release()
    pose.close()
    
    # Filtrar valores válidos y calcular velocidades angulares
    valid_elbow_angles = [a for a in elbow_angles if a is not None]
    valid_forearm_angles = [a for a in forearm_angles if a is not None]
    
    if len(valid_elbow_angles) < 10:  # Mínimo frames requeridos
        print("❌ No hay suficientes frames válidos para análisis de velocidad angular")
        return _fallback_to_extension_method(frames, output_path, is_right_handed)
    
    # Suavizar señales
    smooth_elbow = smooth_signal(valid_elbow_angles, window_size=3)
    smooth_forearm = smooth_signal(valid_forearm_angles, window_size=3)
    
    # Calcular velocidades angulares
    elbow_velocities = calculate_angular_velocity(smooth_elbow, fps)
    forearm_velocities = calculate_angular_velocity(smooth_forearm, fps)
    
    # Encontrar momento de máxima desaceleración (cambio de velocidad)
    # El impacto ocurre cuando la velocidad angular cambia bruscamente
    best_index = _find_impact_by_angular_analysis(
        elbow_velocities, forearm_velocities, stroke_type
    )
    
    # Ajustar índice al array original de frames
    valid_indices = [i for i, a in enumerate(elbow_angles) if a is not None]
    if best_index < len(valid_indices):
        final_index = valid_indices[best_index]
    else:
        final_index = len(frames) // 2  # fallback al centro

    # Buscar frame válido alrededor del índice detectado
    impact_frame = None
    for offset in range(-5, 6):
        idx = final_index + offset
        if 0 <= idx < len(frames):
            rgb = cv2.cvtColor(frames[idx], cv2.COLOR_BGR2RGB)
            with mp_pose.Pose(static_image_mode=True) as fallback_pose:
                result = fallback_pose.process(rgb)
                if result.pose_landmarks and len(result.pose_landmarks.landmark) == 33:
                    impact_frame = frames[idx]
                    print(f"✅ Impact detectado en frame {idx} usando análisis de velocidad angular")
                    break

    if impact_frame is not None:
        out_path = os.path.join(output_path, "impact.jpg")
        cv2.imwrite(out_path, impact_frame)
        print(f"✅ Saved impact frame to {out_path}")
    else:
        print("❌ No valid impact frame found.")

def _find_impact_by_angular_analysis(elbow_velocities, forearm_velocities, stroke_type):
    """
    Encuentra el momento de impacto basado en cambios de velocidad angular.
    El impacto se caracteriza por una desaceleración súbita del movimiento.
    """
    if len(elbow_velocities) < 5:
        return len(elbow_velocities) // 2
    
    # Calcular aceleración angular (cambio de velocidad)
    elbow_accelerations = []
    for i in range(1, len(elbow_velocities)):
        accel = elbow_velocities[i] - elbow_velocities[i-1]
        elbow_accelerations.append(abs(accel))
    
    forearm_accelerations = []
    for i in range(1, len(forearm_velocities)):
        accel = forearm_velocities[i] - forearm_velocities[i-1]
        forearm_accelerations.append(abs(accel))
    
    # Combinar métricas según tipo de golpe
    if stroke_type == "serve":
        # En el saque, el antebrazo es más relevante
        combined_signal = forearm_accelerations
        weight_forearm = 0.7
    else:
        # En forehand/backhand, ambos son importantes
        min_len = min(len(elbow_accelerations), len(forearm_accelerations))
        combined_signal = []
        weight_forearm = 0.6
        
        for i in range(min_len):
            combined = (elbow_accelerations[i] * (1 - weight_forearm) + 
                       forearm_accelerations[i] * weight_forearm)
            combined_signal.append(combined)
    
    if not combined_signal:
        return len(elbow_velocities) // 2
    
    # Encontrar pico de desaceleración (máxima aceleración negativa)
    # Buscar en el tercio medio del swing para evitar ruido inicial/final
    start_search = len(combined_signal) // 4
    end_search = 3 * len(combined_signal) // 4
    
    search_region = combined_signal[start_search:end_search]
    if not search_region:
        return len(elbow_velocities) // 2
    
    max_decel_idx = search_region.index(max(search_region))
    impact_idx = start_search + max_decel_idx
    
    return impact_idx

def _fallback_to_extension_method(frames, output_path, is_right_handed):
    """Método fallback usando máxima extensión (método original)"""
    print("🔄 Usando método fallback de máxima extensión...")
    
    with mp_pose.Pose(static_image_mode=False) as pose:
        max_dist = -1
        best_idx = -1
        
        for i, frame in enumerate(frames):
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = pose.process(rgb)
            
            if not results.pose_landmarks:
                continue
                
            lm = results.pose_landmarks.landmark
            if is_right_handed:
                shoulder, wrist = lm[12], lm[16]
            else:
                shoulder, wrist = lm[11], lm[15]
            
            dist = math.hypot(wrist.x - shoulder.x, wrist.y - shoulder.y)
            if dist > max_dist:
                max_dist = dist
                best_idx = i
        
        if best_idx >= 0:
            out_path = os.path.join(output_path, "impact.jpg")
            cv2.imwrite(out_path, frames[best_idx])
            print(f"✅ Saved impact frame (fallback method) to {out_path}")