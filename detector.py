from ultralytics import YOLO
import cv2
import numpy as np
import json
import threading

model = YOLO("yolov8s.pt")

# Ultralytics models are not guaranteed thread-safe. /detect and /ar-analyze can
# arrive on different Flask threads at the same time, so each inference takes this
# lock. Inference is short (~30-50ms); the lock just prevents concurrent access.
_model_lock = threading.Lock()

FRUIT_CLASS_MAP = {
    "apple":  "apple",
    "banana": "banana",
    "orange": "orange",
}

FRUIT_COLORS = {
    "apple":  (0,   60,  220),
    "banana": (0,   220, 255),
    "orange": (0,   140, 255),
    "kiwi":   (40,  180, 40),
}

CONFIDENCE_THRESHOLD = 0.30


def get_pxcm():
    try:
        with open('config.json') as f:
            return json.load(f).get('pixels_per_cm', 37)
    except Exception:
        return 37


# ══════════════════════════════════════════════════════════
#  MAIN DETECTION  —  single pass, no tiling
# ══════════════════════════════════════════════════════════

def detect_fruits(frame, with_contours=True, with_color=True):
    """
    Single-pass YOLO detection.

    with_contours / with_color: the GrabCut contour and k-means dominant colour
    are by far the most expensive steps (hundreds of ms per fruit). The live
    stream worker disables them and manages contours itself on a budget;
    /analyse and /upload keep the defaults (full pipeline).

    Key parameters explained:
    - iou=0.4  : YOLO's internal NMS threshold. Two boxes of the
                 same class are merged if they overlap MORE than 40%.
                 Side-by-side bananas typically overlap ~20-30% at
                 their edges, so 0.4 keeps them separate.
                 Previous value of 0.6 was too high (kept everything
                 including true duplicates from tiling).
    - conf=0.30: Minimum confidence to report a detection.
    - agnostic_nms=False: Apply NMS per class, not globally.
                 This means two touching bananas compete with each
                 other (good) but a banana and an apple don't (also good).
    - max_det=20: Allow up to 20 detections per image.
    """
    with _model_lock:
        results = model(
            frame,
            verbose=False,
            iou=0.4,
            conf=CONFIDENCE_THRESHOLD,
            agnostic_nms=False,
            max_det=20
        )[0]

    raw = []
    for box in results.boxes:
        confidence = float(box.conf[0])
        class_id   = int(box.cls[0])
        class_name = model.names[class_id].lower()

        if class_name not in FRUIT_CLASS_MAP:
            continue

        fruit_type = FRUIT_CLASS_MAP[class_name]
        x1, y1, x2, y2 = map(int, box.xyxy[0])
        x = max(0, x1)
        y = max(0, y1)
        w = min(frame.shape[1] - x, x2 - x1)
        h = min(frame.shape[0] - y, y2 - y1)

        if w <= 10 or h <= 10:
            continue

        raw.append({
            "fruit_type": fruit_type,
            "confidence": round(confidence * 100, 1),
            "bbox":       (x, y, w, h),
        })

    # Our own dedup as a safety net (catches any remaining true duplicates)
    raw = deduplicate(raw)

    # Build full detection dicts with crops and measurements
    detections = []
    pxcm       = get_pxcm()
    frame_area = frame.shape[0] * frame.shape[1]

    for item in raw:
        x, y, w, h = item["bbox"]
        cropped    = frame[y:y+h, x:x+w]
        if cropped.size == 0:
            continue

        width_cm  = round(w / pxcm, 1)
        height_cm = round(h / pxcm, 1)
        area_cm2  = round((w * h) / (pxcm * pxcm), 1)

        size_info = {
            "width_px":              w,
            "height_px":             h,
            "area_px":               w * h,
            "width_cm":              width_cm,
            "height_cm":             height_cm,
            "area_cm2":              area_cm2,
            "relative_size_percent": round((w * h / frame_area) * 100, 2),
            "estimated_diameter_cm": round((width_cm + height_cm) / 2, 1),
        }

        detections.append({
            "fruit_type":     item["fruit_type"],
            "confidence":     item["confidence"],
            "cropped":        cropped,
            "bbox":           (x, y, w, h),
            "size_info":      size_info,
            "dominant_color": get_dominant_color(cropped) if with_color else None,
            "display_color":  FRUIT_COLORS.get(item["fruit_type"], (180, 180, 180)),
            "contour_points": get_contour_grabcut(cropped) if with_contours else None,
        })

    return detections


# ══════════════════════════════════════════════════════════
#  FAST DETECTION  —  AR pipeline (no GrabCut, no crop)
# ══════════════════════════════════════════════════════════

def detect_fruits_fast(frame, conf=None):
    """
    Lightweight detection for the AR /detect endpoint (~30-50ms).
    Skips GrabCut contour, dominant colour extraction, and image cropping.
    Returns normalised bbox coordinates [x1,y1,x2,y2] (0.0-1.0) so Unity
    can position AR panels without knowing the image resolution.

    conf: optional confidence override. The AR pipeline uses a LOWER threshold
    (0.20) than the web dashboard (0.30) because the Eye camera image is softer
    and a fruit hovering at the threshold makes the AR box flicker in/out.
    """
    with _model_lock:
        results = model(
            frame, verbose=False, iou=0.4,
            conf=conf if conf is not None else CONFIDENCE_THRESHOLD,
            agnostic_nms=False, max_det=20
        )[0]

    h_img, w_img = frame.shape[:2]
    pxcm         = get_pxcm()
    raw          = []

    for box in results.boxes:
        confidence = float(box.conf[0])
        class_id   = int(box.cls[0])
        class_name = model.names[class_id].lower()
        if class_name not in FRUIT_CLASS_MAP:
            continue
        x1, y1, x2, y2 = map(int, box.xyxy[0])
        x = max(0, x1);  y = max(0, y1)
        w = min(w_img - x, x2 - x1)
        h = min(h_img - y, y2 - y1)
        if w <= 10 or h <= 10:
            continue
        raw.append({
            "fruit_type": FRUIT_CLASS_MAP[class_name],
            "confidence": round(confidence * 100, 1),
            "bbox":       (x, y, w, h),
        })

    raw = deduplicate(raw)

    detections = []
    for item in raw:
        x, y, w, h = item["bbox"]
        detections.append({
            "label":      item["fruit_type"],
            "confidence": item["confidence"],
            "bbox_norm":  [                          # normalised 0-1 for Unity
                round(x / w_img, 4),
                round(y / h_img, 4),
                round((x + w) / w_img, 4),
                round((y + h) / h_img, 4),
            ],
            "width_cm":  round(w / pxcm, 1),
            "height_cm": round(h / pxcm, 1),
        })

    return detections


# ══════════════════════════════════════════════════════════
#  DEDUPLICATION  —  safety net only, not primary filter
# ══════════════════════════════════════════════════════════

def deduplicate(detections):
    """
    Removes only TRUE duplicates — boxes of the same fruit type
    whose centres are very close AND overlap heavily.

    This is a safety net for the rare case where YOLO returns
    two nearly identical boxes. It does NOT try to separate
    adjacent fruits (YOLO's iou parameter handles that).

    Sorts by confidence descending — keeps the most confident box.
    """
    detections = sorted(detections, key=lambda d: d["confidence"], reverse=True)
    kept = []

    for det in detections:
        bx, by, bw, bh = det["bbox"]
        b_cx = bx + bw / 2.0
        b_cy = by + bh / 2.0
        is_dup = False

        for k in kept:
            if det["fruit_type"] != k["fruit_type"]:
                continue

            kx, ky, kw, kh = k["bbox"]
            k_cx = kx + kw / 2.0
            k_cy = ky + kh / 2.0

            # Centre distance
            dist  = ((b_cx - k_cx)**2 + (b_cy - k_cy)**2) ** 0.5

            # Only flag as duplicate if centres are very close
            # (within 20% of the smaller box dimension)
            # AND boxes overlap substantially
            close = min(bw, bh, kw, kh) * 0.20
            if dist < close and iou(det["bbox"], k["bbox"]) > 0.50:
                is_dup = True
                break

        if not is_dup:
            kept.append(det)

    return kept


def iou(b1, b2):
    x1,y1,w1,h1 = b1;  x2,y2,w2,h2 = b2
    ix = max(x1,x2);   iy = max(y1,y2)
    iw = min(x1+w1,x2+w2) - ix
    ih = min(y1+h1,y2+h2) - iy
    if iw<=0 or ih<=0: return 0.0
    inter = iw*ih
    return inter / (w1*h1 + w2*h2 - inter) if (w1*h1+w2*h2-inter) > 0 else 0.0


# ══════════════════════════════════════════════════════════
#  GRABCUT CONTOUR
# ══════════════════════════════════════════════════════════

def get_contour_grabcut(cropped):
    """
    GrabCut foreground segmentation for smooth fruit contours.
    Works well on both high-contrast (dark brown banana) and
    low-contrast (green banana on white) cases.
    Falls back to Canny edge detection if GrabCut fails.
    """
    h, w = cropped.shape[:2]
    if w < 20 or h < 20:
        return _canny_fallback(cropped)

    # Resize to max 300px for speed
    scale = 1.0
    if max(w, h) > 300:
        scale = 300.0 / max(w, h)
        img   = cv2.resize(cropped, (int(w*scale), int(h*scale)))
    else:
        img = cropped.copy()

    rh, rw = img.shape[:2]
    mg     = max(4, int(min(rw, rh) * 0.08))
    rect   = (mg, mg, rw - 2*mg, rh - 2*mg)

    if rect[2] <= 0 or rect[3] <= 0:
        return _canny_fallback(cropped)

    mask      = np.zeros((rh, rw), np.uint8)
    bgd_model = np.zeros((1, 65), np.float64)
    fgd_model = np.zeros((1, 65), np.float64)

    try:
        cv2.grabCut(img, mask, rect, bgd_model, fgd_model,
                    iterCount=5, mode=cv2.GC_INIT_WITH_RECT)
    except Exception:
        return _canny_fallback(cropped)

    fruit_mask = np.where(
        (mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0
    ).astype(np.uint8)

    kernel     = np.ones((5,5), np.uint8)
    fruit_mask = cv2.morphologyEx(fruit_mask, cv2.MORPH_CLOSE, kernel)
    fruit_mask = cv2.morphologyEx(fruit_mask, cv2.MORPH_OPEN,  kernel)

    contours, _ = cv2.findContours(
        fruit_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )

    if not contours:
        return _canny_fallback(cropped)

    largest = max(contours, key=cv2.contourArea)
    if cv2.contourArea(largest) < 150:
        return _canny_fallback(cropped)

    if scale < 1.0:
        largest = (largest.astype(np.float32) / scale).astype(np.int32)

    return largest


def _canny_fallback(cropped):
    grey    = cv2.cvtColor(cropped, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(grey, (7,7), 0)
    edges   = cv2.Canny(blurred, 20, 80)
    kernel  = np.ones((3,3), np.uint8)
    edges   = cv2.dilate(edges, kernel, iterations=2)
    contours, _ = cv2.findContours(
        edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    if not contours:
        return None
    largest = max(contours, key=cv2.contourArea)
    return largest if cv2.contourArea(largest) > 200 else None


# ══════════════════════════════════════════════════════════
#  DRAW ON FRAME
# ══════════════════════════════════════════════════════════

def draw_contour_on_frame(frame, detection, index):
    x, y, w, h     = detection["bbox"]
    color          = detection["display_color"]
    contour_points = detection["contour_points"]
    size_info      = detection["size_info"]
    confidence     = detection["confidence"]
    fruit_type     = detection["fruit_type"]

    # Thin bounding box
    cv2.rectangle(frame, (x, y), (x+w, y+h), color, 1)

    # GrabCut contour outline
    if contour_points is not None and len(contour_points) > 0:
        shifted = contour_points + np.array([[[x, y]]])
        cv2.drawContours(frame, [shifted], -1,
                         color=color, thickness=2, lineType=cv2.LINE_AA)

    # Label
    label       = '[{}] {} {:.1f}%'.format(index, fruit_type.upper(), confidence)
    (lw, lh), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.50, 2)
    cv2.rectangle(frame, (x, y-lh-10), (x+lw+8, y), color, -1)
    cv2.putText(frame, label, (x+4, y-5),
                cv2.FONT_HERSHEY_SIMPLEX, 0.50, (255,255,255), 2)

    # Width arrow (below box)
    ay = y + h + 16
    if ay + 14 < frame.shape[0]:
        cv2.arrowedLine(frame, (x,ay),   (x+w,ay), color, 1, tipLength=0.05)
        cv2.arrowedLine(frame, (x+w,ay), (x,ay),   color, 1, tipLength=0.05)
        cv2.putText(frame, '{}cm'.format(size_info['width_cm']),
                    (x+w//2-15, ay+12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, color, 1)

    # Height arrow (right of box)
    ax = x + w + 8
    if ax + 30 < frame.shape[1]:
        cv2.arrowedLine(frame, (ax,y),   (ax,y+h), color, 1, tipLength=0.05)
        cv2.arrowedLine(frame, (ax,y+h), (ax,y),   color, 1, tipLength=0.05)
        cv2.putText(frame, '{}cm'.format(size_info['height_cm']),
                    (ax+3, y+h//2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, color, 1)

    # Number badge
    cx2, cy2 = x+w-14, y+14
    cv2.circle(frame, (cx2, cy2), 13, color, -1)
    cv2.putText(frame, str(index), (cx2-5, cy2+5),
                cv2.FONT_HERSHEY_SIMPLEX, 0.50, (255,255,255), 2)

    return frame


# ══════════════════════════════════════════════════════════
#  COLOUR HELPERS
# ══════════════════════════════════════════════════════════

def get_dominant_color(img):
    pixels   = np.float32(img.reshape(-1, 3))
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0)
    _, labels, centers = cv2.kmeans(
        pixels, 3, None, criteria, 10, cv2.KMEANS_RANDOM_CENTERS
    )
    counts   = np.bincount(labels.flatten())
    dominant = centers[np.argmax(counts)]
    b, g, r  = int(dominant[0]), int(dominant[1]), int(dominant[2])
    return (r, g, b)


def estimate_diameter(w_px, h_px):
    return round(((w_px + h_px) / 2) / get_pxcm(), 1)
