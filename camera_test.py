"""
camera_test.py — run this to diagnose your camera issue
Usage: python camera_test.py
"""
import cv2
import sys

print("=" * 50)
print("CAMERA DIAGNOSTIC TOOL")
print("=" * 50)
print(f"OpenCV version: {cv2.__version__}")
print()

# ── Test 1: Try all camera backends on index 0,1,2 ────────
BACKENDS = [
    (cv2.CAP_DSHOW,   "DirectShow (Windows native)"),
    (cv2.CAP_MSMF,    "Media Foundation (Windows)"),
    (cv2.CAP_ANY,     "Auto-detect"),
]

found = []

for index in range(3):
    for backend, name in BACKENDS:
        print(f"Testing camera index {index} with {name}...", end=" ")
        cap = cv2.VideoCapture(index, backend)

        if not cap.isOpened():
            print("❌ Could not open")
            cap.release()
            continue

        ret, frame = cap.read()
        if not ret or frame is None:
            print("❌ Opened but no frame")
            cap.release()
            continue

        h, w = frame.shape[:2]
        mean  = frame.mean()

        if mean < 5:
            print(f"⚠️  Frame is BLACK (mean={mean:.1f}) — wrong backend or permission issue")
            cap.release()
            continue

        if mean > 200 and frame.std() < 10:
            print(f"⚠️  Frame is SOLID GREEN/WHITE (mean={mean:.1f}) — backend mismatch")
            cap.release()
            continue

        print(f"✅ WORKS — {w}x{h}, mean brightness={mean:.1f}")
        found.append((index, backend, name))
        cap.release()

print()
print("=" * 50)

if not found:
    print("❌ NO WORKING CAMERA FOUND")
    print()
    print("Likely causes:")
    print("  1. Another app (Teams, Zoom, browser) is using the camera")
    print("     → Close all other apps and try again")
    print()
    print("  2. Windows camera privacy setting is blocking Python")
    print("     → Windows Settings → Privacy → Camera")
    print("     → Make sure 'Allow apps to access your camera' is ON")
    print("     → Also enable 'Allow desktop apps to access your camera'")
    print()
    print("  3. Wrong camera driver")
    print("     → Try unplugging and replugging USB webcam")
    print()
else:
    print(f"✅ FOUND {len(found)} WORKING CONFIGURATION(S):")
    print()
    for index, backend, name in found:
        print(f"   Camera index {index}  |  Backend: {name}")

    best_index, best_backend, best_name = found[0]
    print()
    print(f"RECOMMENDATION: Use index={best_index} with backend={best_backend}")
    print()
    print("Copy this into your camera.py get_camera() function:")
    print()

    if best_backend == cv2.CAP_DSHOW:
        print(f"    cap = cv2.VideoCapture({best_index}, cv2.CAP_DSHOW)")
    elif best_backend == cv2.CAP_MSMF:
        print(f"    cap = cv2.VideoCapture({best_index}, cv2.CAP_MSMF)")
    else:
        print(f"    cap = cv2.VideoCapture({best_index})")

print()
print("=" * 50)

# ── Test 2: Show live preview of best working camera ───────
if found:
    print()
    ans = input("Show live camera preview? (y/n): ").strip().lower()
    if ans == 'y':
        index, backend, name = found[0]
        cap = cv2.VideoCapture(index, backend)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        print(f"Opening camera {index} ({name}) — press Q to close")

        while True:
            ret, frame = cap.read()
            if not ret:
                print("Lost camera feed")
                break
            cv2.imshow("Camera Test — press Q to quit", frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

        cap.release()
        cv2.destroyAllWindows()
        print("Preview closed.")
