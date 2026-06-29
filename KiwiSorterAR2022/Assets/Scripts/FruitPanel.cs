// FruitPanel.cs
// One AR overlay = a coloured bounding box around the fruit + a label card above it.
// Root RectTransform is sized/positioned to the detection box each frame.

using UnityEngine;
using UnityEngine.UI;
using TMPro;

public class FruitPanel : MonoBehaviour
{
    // Set by FruitDetectionManager every frame so the placeholder text is honest:
    // "analysing..." only when the Claude pipeline is actually running.
    public static bool AnalyzeOn = true;

    [Header("Bounding box")]
    public Image boxFill;                       // very transparent fill
    public Image edgeTop, edgeBottom, edgeLeft, edgeRight;  // coloured outline

    [Header("Label card")]
    public RectTransform labelCard;
    public TextMeshProUGUI labelText;
    public TextMeshProUGUI sizeText;

    [Header("Quality (slow pipeline)")]
    public GameObject qualitySection;
    public TextMeshProUGUI qualityText;
    public TextMeshProUGUI decayText;
    public TextMeshProUGUI daysText;

    // Dumb placement: the manager (FruitDetectionManager.RenderTracks) does ALL the
    // coordinate math (letterbox, FOV zoom, mounting offset, head reprojection) in one
    // clean ordered pass so every calibration knob is independent. Here we just place
    // the box. anchoredPos is relative to canvas centre; sizePx is the box size in px.
    public void UpdatePanel(DetectionData det, AnalysisData qa,
                            Vector2 anchoredPos, Vector2 sizePx)
    {
        var rt = GetComponent<RectTransform>();
        rt.anchoredPosition = anchoredPos;
        rt.sizeDelta        = sizePx;

        // Colour the box to the fruit
        Color32 col = GetFruitColor(det.label);
        if (boxFill)    boxFill.color    = new Color32(col.r, col.g, col.b, 28);
        if (edgeTop)    edgeTop.color    = col;
        if (edgeBottom) edgeBottom.color = col;
        if (edgeLeft)   edgeLeft.color   = col;
        if (edgeRight)  edgeRight.color  = col;

        // Fast section
        if (labelText) labelText.text =
            GetIcon(det.label) + " " + det.label.ToUpper() + "  " + det.confidence.ToString("F0") + "%";

        // Size-in-cm is HIDDEN in AR: the px/cm calibration assumes a fixed webcam
        // distance, so the numbers are wrong through the Eye camera. This line shows
        // quality info instead (defects once Claude has answered).
        if (sizeText)
        {
            if (qa != null)
            {
                string defects = string.IsNullOrEmpty(qa.defects) || qa.defects == "None"
                    ? "No visible defects" : "Defects: " + qa.defects;
                if (defects.Length > 90) defects = defects.Substring(0, 90) + "…";
                sizeText.text = defects;
            }
            else sizeText.text = AnalyzeOn ? "Quality: analysing..." : "AI quality: off";
        }

        // Slow section (Claude)
        if (qualitySection) qualitySection.SetActive(qa != null);
        if (qa != null)
        {
            if (qualityText) { qualityText.text = qa.quality ?? "?"; qualityText.color = GetQualityColor(qa.quality); }
            if (decayText)   decayText.text = qa.decay_stage ?? "?";
            if (daysText)    { daysText.text = qa.days_remaining + "d left"; daysText.color = GetDaysColor(qa.days_remaining); }
        }
    }

    string GetIcon(string label)
    {
        if (label == null) return "?";
        switch (label.ToLower())
        {
            case "apple":  return "[Apple]";
            case "banana": return "[Banana]";
            case "orange": return "[Orange]";
            case "kiwi":   return "[Kiwi]";
            default:       return "[Fruit]";
        }
    }

    Color32 GetFruitColor(string label)
    {
        if (label == null) return new Color32(150, 150, 150, 255);
        switch (label.ToLower())
        {
            case "apple":  return new Color32(220, 48,  48,  255);
            case "banana": return new Color32(245, 192, 0,   255);
            case "orange": return new Color32(255, 120, 0,   255);
            case "kiwi":   return new Color32(60,  180, 48,  255);
            default:       return new Color32(150, 150, 150, 255);
        }
    }

    Color32 GetQualityColor(string q)
    {
        if (q == "Good")       return new Color32(76,  175, 80,  255);
        if (q == "Acceptable") return new Color32(255, 193, 7,   255);
        if (q == "Poor")       return new Color32(255, 152, 0,   255);
        if (q == "Bad")        return new Color32(244, 67,  54,  255);
        return new Color32(200, 200, 200, 255);
    }

    Color32 GetDaysColor(string days)
    {
        int d;
        if (int.TryParse(days, out d))
        {
            if (d <= 1) return new Color32(244, 67, 54, 255);
            if (d <= 3) return new Color32(255, 152, 0, 255);
            return new Color32(76, 175, 80, 255);
        }
        return new Color32(200, 200, 200, 255);
    }
}
