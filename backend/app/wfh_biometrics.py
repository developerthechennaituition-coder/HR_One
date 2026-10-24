"""Local face matching and challenge-response liveness helpers for WFH attendance."""

from __future__ import annotations

import io
import json
import math
import os
from typing import Any


MAX_FRAME_BYTES = 180_000
MIN_LIVE_FRAMES = 8
MAX_LIVE_FRAMES = 16
DEFAULT_FACE_MATCH_TOLERANCE = 0.48


def _configured_face_match_tolerance() -> float:
    configured = os.getenv(
        "WFH_FACE_MATCH_TOLERANCE",
        str(DEFAULT_FACE_MATCH_TOLERANCE),
    )
    try:
        tolerance = float(configured)
    except ValueError as exc:
        raise RuntimeError(
            "WFH_FACE_MATCH_TOLERANCE must be greater than 0 and no more than 0.48."
        ) from exc
    if (
        not math.isfinite(tolerance)
        or tolerance <= 0
        or tolerance > DEFAULT_FACE_MATCH_TOLERANCE
    ):
        raise RuntimeError(
            "WFH_FACE_MATCH_TOLERANCE must be greater than 0 and no more than 0.48."
        )
    return tolerance


FACE_MATCH_TOLERANCE = _configured_face_match_tolerance()


def decode_frames(encoded_frames: list[str]) -> list[bytes]:
    import base64
    import binascii

    if not MIN_LIVE_FRAMES <= len(encoded_frames) <= MAX_LIVE_FRAMES:
        raise ValueError("Capture must contain between 8 and 16 camera frames.")

    frames = []
    for encoded in encoded_frames:
        value = encoded.split(",", 1)[-1]
        try:
            frame = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("A camera frame could not be read. Please try again.") from exc
        if not frame or len(frame) > MAX_FRAME_BYTES:
            raise ValueError("Camera frame size is invalid. Please try again.")
        frames.append(frame)
    return frames


def _vision_modules() -> tuple[Any, Any, Any]:
    try:
        import face_recognition
        import mediapipe as mp
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            "Local face verification is not installed. Install the WFH biometric dependencies."
        ) from exc
    return face_recognition, mp, np


def _fernet():
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:
        raise RuntimeError(
            "Local face-template encryption is not installed."
        ) from exc

    try:
        key = os.getenv("WFH_TEMPLATE_ENCRYPTION_KEY", "").encode("ascii")
    except UnicodeEncodeError as exc:
        raise RuntimeError(
            "WFH_TEMPLATE_ENCRYPTION_KEY must be a valid Fernet key."
        ) from exc
    if not key:
        raise RuntimeError("WFH_TEMPLATE_ENCRYPTION_KEY is not configured.")
    try:
        return Fernet(key)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "WFH_TEMPLATE_ENCRYPTION_KEY must be a valid Fernet key."
        ) from exc


def _eye_ratio(landmarks: Any, points: tuple[int, ...], np: Any) -> float:
    first, upper_a, upper_b, last, lower_b, lower_a = points
    width = np.linalg.norm(
        np.array(landmarks[first][:2]) - np.array(landmarks[last][:2])
    )
    if width <= 0:
        return 0.0
    vertical_a = np.linalg.norm(
        np.array(landmarks[upper_a][:2]) - np.array(landmarks[lower_a][:2])
    )
    vertical_b = np.linalg.norm(
        np.array(landmarks[upper_b][:2]) - np.array(landmarks[lower_b][:2])
    )
    return float((vertical_a + vertical_b) / (2 * width))


def _frame_observations(frames: list[bytes]) -> tuple[list[Any], list[float]]:
    face_recognition, mp, np = _vision_modules()
    face_mesh = mp.solutions.face_mesh.FaceMesh(
        static_image_mode=True,
        max_num_faces=2,
        refine_landmarks=True,
        min_detection_confidence=0.5,
    )
    embeddings: list[Any] = []
    yaw_values: list[float] = []
    blink_values: list[float] = []

    try:
        for frame_bytes in frames:
            try:
                image = face_recognition.load_image_file(io.BytesIO(frame_bytes))
            except (OSError, ValueError) as exc:
                raise ValueError(
                    "A camera frame could not be read. Please try again."
                ) from exc

            result = face_mesh.process(image)
            if not result.multi_face_landmarks:
                continue
            if len(result.multi_face_landmarks) != 1:
                raise ValueError(
                    "Keep only one face in view during camera verification."
                )

            landmarks = [
                (item.x, item.y, item.z)
                for item in result.multi_face_landmarks[0].landmark
            ]
            height, width = image.shape[:2]
            x_values = [point[0] * width for point in landmarks]
            y_values = [point[1] * height for point in landmarks]
            left, right = min(x_values), max(x_values)
            top, bottom = min(y_values), max(y_values)
            padding = int(max(right - left, bottom - top) * 0.12)
            mesh_location = (
                max(0, int(top) - padding),
                min(width, int(right) + padding),
                min(height, int(bottom) + padding),
                max(0, int(left) - padding),
            )
            locations = face_recognition.face_locations(
                image,
                number_of_times_to_upsample=2,
                model="hog",
            )
            location = locations[0] if len(locations) == 1 else mesh_location
            encodings = face_recognition.face_encodings(
                image,
                known_face_locations=[location],
                num_jitters=1,
            )
            if len(encodings) != 1:
                continue
            embeddings.append(encodings[0])

            eye_left = _eye_ratio(landmarks, (33, 160, 158, 133, 153, 144), np)
            eye_right = _eye_ratio(landmarks, (362, 385, 387, 263, 373, 380), np)
            blink_values.append((eye_left + eye_right) / 2)

            eye_midpoint = (landmarks[33][0] + landmarks[263][0]) / 2
            eye_width = abs(landmarks[33][0] - landmarks[263][0])
            if eye_width <= 0:
                raise ValueError("Face liveness could not be verified. Please try again.")
            yaw_values.append((landmarks[1][0] - eye_midpoint) / eye_width)
    finally:
        face_mesh.close()

    if len(embeddings) < MIN_LIVE_FRAMES:
        raise ValueError(
            "Face verification could not read enough camera frames. "
            "Face the camera, improve lighting, and try again."
        )

    return embeddings, list(zip(blink_values, yaw_values))


def _verify_liveness(observations: list[Any], actions: list[str]) -> None:
    if tuple(actions) not in {
        ("blink", "turn_left"),
        ("blink", "turn_right"),
        ("turn_left", "blink"),
        ("turn_right", "blink"),
    }:
        raise ValueError("The camera liveness challenge is invalid.")

    blinks = [item[0] for item in observations]
    yaws = [item[1] for item in observations]

    open_eye_ratio = sorted(blinks)[len(blinks) // 2]
    open_threshold = open_eye_ratio * 0.9
    closed_threshold = open_eye_ratio * 0.82
    blink_positions = [
        index
        for index, ratio in enumerate(blinks)
        if ratio < closed_threshold
        and any(value >= open_threshold for value in blinks[:index])
        and any(value >= open_threshold for value in blinks[index + 1 :])
    ]
    if not blink_positions:
        raise ValueError(
            "Blink check failed. Blink once while facing the camera, "
            "then follow the head-turn prompt."
        )

    baseline = sum(yaws[:2]) / 2
    turn_positions = [
        index
        for index, value in enumerate(yaws[2:-2], start=2)
        if abs(value - baseline) >= 0.06
    ]
    returned_to_center = abs(sum(yaws[-2:]) / 2 - baseline) <= 0.12
    if not turn_positions or not returned_to_center:
        raise ValueError(
            "Head-turn check failed. Turn your head when prompted, "
            "then face the camera again."
        )

    blink_position = blink_positions[0]
    turn_position = turn_positions[0]
    if (
        actions[0] == "blink" and blink_position >= turn_position
    ) or (
        actions[0] != "blink" and turn_position >= blink_position
    ):
        raise ValueError(
            "Camera challenge failed. Please follow the requested actions in order."
        )


def create_face_template(frames: list[bytes], actions: list[str]) -> list[float]:
    _, _, np = _vision_modules()
    embeddings, observations = _frame_observations(frames)
    _verify_liveness(observations, actions)
    template = np.mean(np.asarray(embeddings), axis=0)
    if not all(math.isfinite(float(value)) for value in template):
        raise ValueError("Face registration failed. Please try again.")
    return np.asarray(template, dtype=float).tolist()


def verify_face_template(
    encrypted_template: bytes,
    frames: list[bytes],
    actions: list[str],
) -> None:
    try:
        from cryptography.fernet import InvalidToken
    except ImportError as exc:
        raise RuntimeError(
            "Local face-template encryption is not installed."
        ) from exc

    face_recognition, _, np = _vision_modules()
    try:
        stored = json.loads(_fernet().decrypt(encrypted_template).decode("utf-8"))
    except (InvalidToken, ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError("The registered face template could not be decrypted.") from exc
    if not isinstance(stored, list) or len(stored) != 128:
        raise RuntimeError("The registered face template is invalid.")
    if not all(
        isinstance(value, (int, float)) and math.isfinite(value)
        for value in stored
    ):
        raise RuntimeError("The registered face template is invalid.")

    embeddings, observations = _frame_observations(frames)
    _verify_liveness(observations, actions)
    expected = np.asarray(stored, dtype=float)
    distances = face_recognition.face_distance(embeddings, expected)
    if not len(distances) or float(np.median(distances)) > FACE_MATCH_TOLERANCE:
        raise ValueError("Face verification failed. Please try again.")


def encrypt_face_template(template: list[float]) -> bytes:
    return _fernet().encrypt(json.dumps(template).encode("utf-8"))


def encrypt_face_photo(photo_bytes: bytes) -> bytes:
    """Encrypt a retained registration photograph with the existing biometric key."""
    if not photo_bytes or len(photo_bytes) > MAX_FRAME_BYTES:
        raise ValueError("Face photograph size is invalid.")
    return _fernet().encrypt(photo_bytes)


def decrypt_face_photo(encrypted_photo: bytes) -> bytes:
    """Decrypt a retained registration photograph for authorized server-side use."""
    try:
        from cryptography.fernet import InvalidToken
    except ImportError as exc:
        raise RuntimeError(
            "Local face-template encryption is not installed."
        ) from exc
    try:
        photo = _fernet().decrypt(encrypted_photo)
    except (InvalidToken, TypeError, ValueError) as exc:
        raise RuntimeError("The registered face photograph could not be decrypted.") from exc
    if not photo or len(photo) > MAX_FRAME_BYTES:
        raise RuntimeError("The registered face photograph is invalid.")
    return photo
