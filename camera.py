"""
camera.py — handles camera opening with correct Windows backend

The green screen / no detection problem on Windows is almost always
caused by OpenCV using the wrong camera backend.

Windows has two camera APIs:
  - DirectShow (CAP_DSHOW)   — older but very reliable, works with
                               almost all webcams and laptop cameras
  - Media Foundation (MSMF) — newer Windows API, sometimes causes
                               green frames or black frames with OpenCV

By default OpenCV on Windows tries MSMF first, which often fails.
We force DirectShow which is universally compatible.
"""
import cv2


def get_camera(index=0):
    """
    Opens camera using DirectShow backend on Windows.
    Falls back through other backends if DirectShow fails.
    """
    backends = [
        (cv2.CAP_DSHOW, "DirectShow"),
        (cv2.CAP_MSMF,  "Media Foundation"),
        (cv2.CAP_ANY,   "Auto"),
    ]

    for backend, name in backends:
        cap = cv2.VideoCapture(index, backend)

        if not cap.isOpened():
            cap.release()
            continue

        # Test that we actually get a real frame
        ret, frame = cap.read()
        if not ret or frame is None:
            cap.release()
            continue

        # Check frame is not solid green or black (bad backend signs)
        mean = frame.mean()
        std  = frame.std()
        if mean < 5 or (mean > 200 and std < 10):
            cap.release()
            continue

        # Good frame — set resolution and return
        print(f"Camera {index} opened with {name} backend")
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

        # Discard a few frames — some cameras send garbage on startup
        for _ in range(5):
            cap.read()

        return cap

    raise RuntimeError(
        f"Could not open camera index {index} with any backend.\n"
        "Try:\n"
        "  1. Close all other apps using the camera (Teams, Zoom, browser)\n"
        "  2. Windows Settings → Privacy → Camera → enable Desktop app access\n"
        "  3. Run camera_test.py to find the correct index\n"
        "  4. Try index 1 or 2 instead of 0"
    )


def capture_frame(cap):
    """Captures a single frame. Retries once if first attempt fails."""
    ret, frame = cap.read()
    if not ret or frame is None:
        # Try once more
        ret, frame = cap.read()
    if not ret or frame is None:
        raise RuntimeError("Failed to capture frame from camera.")
    return frame


def release_camera(cap):
    if cap is not None and cap.isOpened():
        cap.release()
    cv2.destroyAllWindows()
