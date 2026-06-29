// DataModels.cs — JSON data structures matching the Flask API responses.
// Keep in sync with /detect and /ar-analyze endpoint schemas in app.py.
using System;

// ── /detect response ────────────────────────────────────────────────────────
[Serializable]
public class DetectionData
{
    public string  label;        // "apple" | "banana" | "orange" | "kiwi"
    public float   confidence;   // 0–100
    public float[] bbox_norm;    // [x1, y1, x2, y2] normalised 0–1
    public float   width_cm;
    public float   height_cm;
}

[Serializable]
public class DetectResponse
{
    public DetectionData[] detections;
    public double          ts;
}

// ── /ar-analyze response ─────────────────────────────────────────────────────
[Serializable]
public class AnalysisData
{
    public string label;
    public string quality;         // "Good" | "Acceptable" | "Poor" | "Bad"
    public string decay_stage;     // "Fresh" | "Ripe" | "Overripe" | "Decaying"
    public string days_remaining;  // string from Claude, e.g. "5"
    public string recommendation;
    public string confidence_ai;   // "High" | "Medium" | "Low"
    public string defects;
}

[Serializable]
public class AnalyzeResponse
{
    public DetectionData[] detections;
    public AnalysisData[]  analysis;
    public double          ts;
}
