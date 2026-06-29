import cv2
import os
import tkinter as tk
from tkinter import filedialog
from camera import get_camera, capture_frame, release_camera
from detector import detect_fruits
from analyser import analyse_fruit
from display import (draw_detection, draw_summary_panel,
                     draw_no_fruit, draw_instructions)


def save_result(frame, detections, analyses, counter):
    os.makedirs("results", exist_ok=True)
    fruits = "_".join(d["fruit_type"] for d in detections)
    filename = f"results/scan_{counter:03d}_{fruits}.jpg"
    cv2.imwrite(filename, frame)
    print(f"Saved: {filename}")


def pick_image_file():
    """Opens a file picker dialog and returns the selected image path."""
    root = tk.Tk()
    root.withdraw()  # hide the tiny tk window
    root.attributes("-topmost", True)
    path = filedialog.askopenfilename(
        title="Select a fruit image",
        filetypes=[
            ("Image files", "*.jpg *.jpeg *.png *.bmp *.webp"),
            ("All files", "*.*"),
        ]
    )
    root.destroy()
    return path


def run_image_mode():
    """
    Loads an image from disk, runs detection + Claude analysis,
    and shows the result in a window. Press any key to close.
    """
    print("\nOpening file picker...")
    path = pick_image_file()

    if not path:
        print("No file selected.")
        return

    print(f"Loading: {path}")
    frame = cv2.imread(path)

    if frame is None:
        print("Could not load image. Make sure it's a valid image file.")
        return

    # Resize if too large for screen
    max_w, max_h = 1280, 720
    h, w = frame.shape[:2]
    if w > max_w or h > max_h:
        scale = min(max_w / w, max_h / h)
        frame = cv2.resize(frame, (int(w * scale), int(h * scale)))

    detections = detect_fruits(frame)

    if not detections:
        print("No fruits detected in image.")
        print("Tip: Try a clearer photo with good lighting and a plain background.")
        cv2.putText(frame,
                    "No fruit detected - try another image (better lighting/background)",
                    (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 255), 2)
        cv2.imshow("Image Analysis", frame)
        cv2.waitKey(0)
        cv2.destroyAllWindows()
        return

    print(f"Detected {len(detections)} fruit(s) — analysing with Claude...\n")
    analyses = []

    for det in detections:
        print(f"  → Analysing {det['fruit_type']}...")
        try:
            analysis = analyse_fruit(
                det["cropped"],
                det["size_info"],
                det["dominant_color"],
                det["fruit_type"],
            )
            analyses.append(analysis)
            print(f"     {det['fruit_type'].upper()}: "
                  f"{analysis.get('QUALITY')} | "
                  f"{analysis.get('DECAY_STAGE')} | "
                  f"{analysis.get('DAYS_REMAINING')} days remaining")
        except Exception as e:
            print(f"     API error: {e}")
            analyses.append({})

    # Draw results on image
    for det, analysis in zip(detections, analyses):
        frame = draw_detection(frame, det, analysis)

    frame = draw_summary_panel(frame, detections, analyses)

    # Instructions bar at bottom
    h = frame.shape[0]
    cv2.rectangle(frame, (0, h - 35), (frame.shape[1], h), (40, 40, 40), -1)
    cv2.putText(frame, "S: Save result  |  Any key / close window to exit",
                (10, h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1)

    cv2.imshow("Image Analysis", frame)

    # Save automatically to results folder
    os.makedirs("results", exist_ok=True)
    basename = os.path.splitext(os.path.basename(path))[0]
    out_path = f"results/{basename}_analysed.jpg"
    cv2.imwrite(out_path, frame)
    print(f"\nResult saved to: {out_path}")

    key = cv2.waitKey(0) & 0xFF
    if key == ord('s'):
        print(f"Already saved to: {out_path}")

    cv2.destroyAllWindows()


def main():
    print("🍎🍌🍊🥝 Multi-Fruit Quality Sorter")
    print("Starting camera...")

    cap = get_camera(index=0)

    last_detections = []
    last_analyses = []
    save_counter = 0

    print("Camera ready!")
    print("SPACE = analyse | I = upload image | S = save | Q = quit\n")

    while True:
        frame = capture_frame(cap)
        detections = detect_fruits(frame)

        if detections:
            for i, det in enumerate(detections):
                analysis = last_analyses[i] if i < len(last_analyses) else {}
                frame = draw_detection(frame, det, analysis)

            if last_analyses:
                frame = draw_summary_panel(frame, detections, last_analyses)
            else:
                cv2.putText(frame,
                            f"{len(detections)} fruit(s) detected - press SPACE to analyse",
                            (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 0), 2)
        else:
            frame = draw_no_fruit(frame)
            last_analyses = []

        frame = draw_instructions(frame)
        cv2.imshow("Multi-Fruit Sorter", frame)

        key = cv2.waitKey(1) & 0xFF

        # SPACE — analyse all detected fruits via camera
        if key == ord(' ') and detections:
            print(f"\nAnalysing {len(detections)} fruit(s)...")
            last_detections = detections
            last_analyses = []

            for det in detections:
                print(f"  → Sending {det['fruit_type']} to Claude API...")
                try:
                    analysis = analyse_fruit(
                        det["cropped"],
                        det["size_info"],
                        det["dominant_color"],
                        det["fruit_type"],
                    )
                    last_analyses.append(analysis)
                    print(f"     {det['fruit_type'].upper()}: "
                          f"{analysis.get('QUALITY')} | "
                          f"{analysis.get('DECAY_STAGE')} | "
                          f"{analysis.get('DAYS_REMAINING')} days")
                except Exception as e:
                    print(f"     API error for {det['fruit_type']}: {e}")
                    last_analyses.append({})

        # I — upload and analyse an image file
        elif key == ord('i'):
            cv2.destroyAllWindows()      # close camera window temporarily
            run_image_mode()
            cv2.namedWindow("Multi-Fruit Sorter")  # reopen camera window

        # S — save screenshot
        elif key == ord('s') and last_analyses:
            save_result(frame, last_detections, last_analyses, save_counter)
            save_counter += 1

        # Q — quit
        elif key == ord('q'):
            print("Exiting...")
            break

    release_camera(cap)


if __name__ == "__main__":
    main()