import cv2

QUALITY_COLORS = {
    "Good":       (0, 200, 0),
    "Acceptable": (0, 200, 255),
    "Poor":       (0, 100, 255),
    "Bad":        (0, 0, 220),
    "Unknown":    (180, 180, 180),
}

SORT_LABELS = {
    "1": "USE IMMEDIATELY",
    "2": "USE SOON",
    "3": "STORE",
    "4": "DISCARD",
}

FRUIT_EMOJI = {
    "kiwi":   "KIWI",
    "apple":  "APPLE",
    "banana": "BANANA",
    "orange": "ORANGE",
}


def draw_detection(frame, detection, analysis):
    """Draws bounding box and label for a single detected fruit."""
    x, y, w, h = detection["bbox"]
    fruit_type = detection["fruit_type"]
    quality = analysis.get("QUALITY", "Unknown")

    box_color = detection["display_color"]
    quality_color = QUALITY_COLORS.get(quality, (180, 180, 180))

    # Bounding box
    cv2.rectangle(frame, (x, y), (x + w, y + h), box_color, 3)

    # Label above box
    label = f"{FRUIT_EMOJI.get(fruit_type, fruit_type).upper()} | {quality} | {analysis.get('DECAY_STAGE', '?')}"
    (lw, lh), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
    cv2.rectangle(frame, (x, y - lh - 14), (x + lw + 10, y), box_color, -1)
    cv2.putText(frame, label, (x + 5, y - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

    # Days remaining badge
    days = analysis.get("DAYS_REMAINING", "?")
    badge = f"{days}d left"
    cv2.rectangle(frame, (x, y + h), (x + 90, y + h + 28), quality_color, -1)
    cv2.putText(frame, badge, (x + 5, y + h + 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

    return frame


def draw_summary_panel(frame, detections, analyses):
    """Draws a summary panel listing all detected fruits."""
    if not detections:
        return frame

    panel_w = 400
    line_h = 26
    padding = 10
    total_lines = sum(6 for _ in detections) + 1
    panel_h = total_lines * line_h + padding * 2

    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (panel_w, panel_h), (20, 20, 20), -1)
    cv2.addWeighted(overlay, 0.65, frame, 0.35, 0, frame)

    y_pos = padding + line_h
    cv2.putText(frame, f"DETECTED: {len(detections)} fruit(s)",
                (padding, y_pos), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 0), 2)
    y_pos += line_h + 4

    for i, (det, analysis) in enumerate(zip(detections, analyses)):
        color = det["display_color"]
        fruit = det["fruit_type"].upper()
        sort = SORT_LABELS.get(analysis.get("SORT_PRIORITY", "?"), "?")

        lines = [
            f"[{i+1}] {fruit}",
            f"    Quality:  {analysis.get('QUALITY', '?')}",
            f"    Stage:    {analysis.get('DECAY_STAGE', '?')}",
            f"    Days:     {analysis.get('DAYS_REMAINING', '?')}",
            f"    Sort:     {sort}",
            f"    Size:     {det['size_info']['estimated_diameter_cm']}cm"
                f" ({analysis.get('SIZE_CATEGORY', '?')})",
        ]

        for j, line in enumerate(lines):
            text_color = color if j == 0 else (220, 220, 220)
            thickness = 2 if j == 0 else 1
            cv2.putText(frame, line, (padding, y_pos),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, text_color, thickness)
            y_pos += line_h

        y_pos += 4  # gap between fruits

    return frame


def draw_no_fruit(frame):
    cv2.putText(frame, "No fruit detected - place fruit in view",
                (30, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (100, 100, 255), 2)
    return frame


def draw_instructions(frame):
    h = frame.shape[0]
    cv2.rectangle(frame, (0, h - 40), (frame.shape[1], h), (40, 40, 40), -1)
    cv2.putText(frame,
                "SPACE: Analyse  |  I: Upload image  |  S: Save  |  Q: Quit",
                (10, h - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1)
    return frame