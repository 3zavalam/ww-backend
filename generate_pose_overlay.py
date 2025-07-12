import cv2
import mediapipe as mp
import os
import shutil

mp_pose = mp.solutions.pose

def generate_pose_overlay(input_path, output_path, codecs=None):
    cap = None
    out = None
    pose = None
    try:
        cap = cv2.VideoCapture(input_path)
        if not cap.isOpened():
            raise Exception(f"Could not open video: {input_path}")

        width  = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps    = cap.get(cv2.CAP_PROP_FPS)

        codecs = codecs or ["mp4v", "MJPG", "DIVX", "XVID"]

        # Intentar abrir un VideoWriter con cada códec hasta que funcione
        out = None
        for codec in codecs:
            try:
                fourcc = cv2.VideoWriter_fourcc(*codec)
                tentative_out = cv2.VideoWriter(output_path, fourcc, fps, (width, height))
                if tentative_out.isOpened():
                    out = tentative_out
                    break
                else:
                    tentative_out.release()
            except Exception:
                # Podría fallar si el códec no está disponible; continuamos con el siguiente
                continue

        if out is None:
            raise Exception("No se pudo abrir el video para escritura con ninguno de los códecs especificados.")

        pose = mp_pose.Pose()

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = pose.process(rgb)

            if results.pose_landmarks:
                mp.solutions.drawing_utils.draw_landmarks(
                    frame, results.pose_landmarks, mp_pose.POSE_CONNECTIONS)

            out.write(frame)

        # Asegurar que el video se cierre correctamente
        if out:
            out.release()
            out = None
        if cap:
            cap.release()
            cap = None
        if pose:
            pose.close()
            pose = None

        # Verificar que el video sea válido
        test_cap = cv2.VideoCapture(output_path)
        if test_cap.isOpened():
            test_cap.release()
        else:
            test_cap.release()
            # Si el video no es válido, copia el original
            shutil.copy(input_path, output_path)

    except Exception as e:
        print(f"Error generando el video con pose: {e}. Se usará el video original.")
        # Si algo falla, nos aseguramos de que los recursos se liberen antes de copiar
        if cap: cap.release()
        if out: out.release()
        if pose: pose.close()
        # Copiamos el original como fallback
        shutil.copy(input_path, output_path)
        # Re-asignamos a None para que el bloque finally no intente liberarlos de nuevo
        cap, out, pose = None, None, None

    finally:
        if pose:
            pose.close()
        if cap:
            cap.release()
        if out:
            out.release()