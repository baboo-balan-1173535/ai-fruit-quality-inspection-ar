// KiwiSceneBuilder.cs — Editor automation.
//   KiwiSorter > Build AR Scene            : builds the whole scene + prefab
//   KiwiSorter > Rebuild FruitPanel Prefab : rebuilds only the prefab (keeps scene)

using UnityEngine;
using UnityEngine.UI;
using UnityEditor;
using TMPro;
using System.IO;

public static class KiwiSceneBuilder
{
    const string PrefabPath = "Assets/Prefabs/FruitPanel.prefab";

    [MenuItem("KiwiSorter/Build AR Scene")]
    public static void BuildScene()
    {
        // Canvas
        var canvasGO = new GameObject("Canvas",
            typeof(Canvas), typeof(CanvasScaler), typeof(GraphicRaycaster));
        var canvas = canvasGO.GetComponent<Canvas>();
        canvas.renderMode = RenderMode.ScreenSpaceOverlay;
        var scaler = canvasGO.GetComponent<CanvasScaler>();
        scaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
        scaler.referenceResolution = new Vector2(1920, 1080);   // landscape
        scaler.matchWidthOrHeight = 0.5f;

        // CameraBackground
        var bgGO = new GameObject("CameraBackground", typeof(RawImage));
        bgGO.transform.SetParent(canvasGO.transform, false);
        var bgImg = bgGO.GetComponent<RawImage>();
        bgImg.color = Color.white;
        StretchFull(bgGO.GetComponent<RectTransform>());

        // PanelParent
        var parentGO = new GameObject("PanelParent", typeof(RectTransform));
        parentGO.transform.SetParent(canvasGO.transform, false);
        StretchFull(parentGO.GetComponent<RectTransform>());

        // EventSystem
        if (Object.FindObjectOfType<UnityEngine.EventSystems.EventSystem>() == null)
            new GameObject("EventSystem",
                typeof(UnityEngine.EventSystems.EventSystem),
                typeof(UnityEngine.EventSystems.StandaloneInputModule));

        var prefab = BuildFruitPanelPrefab();

        // FruitDetectionManager
        var mgrGO = new GameObject("FruitDetectionManager");
        var mgr   = mgrGO.AddComponent<FruitDetectionManager>();
        mgr.cameraBackground = bgImg;
        mgr.panelParent      = parentGO.GetComponent<RectTransform>();
        mgr.fruitPanelPrefab = prefab;

        MarkDirty();
        EditorUtility.DisplayDialog("Kiwi Sorter",
            "AR scene built and wired.\nSave (Ctrl+S), then Build.", "OK");
    }

    [MenuItem("KiwiSorter/Setup Mirror Mode (No ControlGlasses)")]
    public static void SetupMirrorMode()
    {
        // Reverts the scene to a plain 2D phone app. The glasses mirror the phone
        // screen in their native display mode — no XREAL SDK / ControlGlasses / MRSpace.

        // 1. Remove the XREAL XR rig if present
        foreach (var name in new[] { "XR Interaction Setup", "XR Origin (XR Rig)" })
        {
            var rig = GameObject.Find(name);
            if (rig != null) Object.DestroyImmediate(rig);
        }

        // 2. Ensure a plain Main Camera exists (XR setup deleted it)
        Camera cam = null;
        foreach (var c in Object.FindObjectsOfType<Camera>())
            if (c.gameObject.name == "Main Camera") { cam = c; break; }
        if (cam == null)
        {
            var camGO = new GameObject("Main Camera", typeof(Camera), typeof(AudioListener));
            camGO.tag = "MainCamera";
            cam = camGO.GetComponent<Camera>();
        }
        cam.clearFlags = CameraClearFlags.SolidColor;
        cam.backgroundColor = Color.black;

        // 3. Canvas back to Screen Space Overlay (renders directly to the phone screen)
        var canvas = Object.FindObjectOfType<Canvas>();
        if (canvas != null)
        {
            canvas.renderMode  = RenderMode.ScreenSpaceOverlay;
            canvas.worldCamera = null;
        }

        // 4. Show the camera feed full-screen again
        var bg = GameObject.Find("CameraBackground");
        if (bg != null) bg.SetActive(true);

        MarkDirty();
        EditorUtility.DisplayDialog("Kiwi Sorter",
            "Mirror mode set up (no ControlGlasses):\n" +
            "- XREAL rig removed, plain camera restored\n" +
            "- Camera feed shown full-screen\n" +
            "- Canvas renders to phone screen\n\n" +
            "IMPORTANT: also untick XREAL in\n" +
            "Project Settings > XR Plug-in Management > Android.\n\n" +
            "Then Save (Ctrl+S) and Build.", "OK");
    }

    [MenuItem("KiwiSorter/Setup Glasses (Optical AR)")]
    public static void SetupGlasses()
    {
        // 1. Add the XREAL rig if not already present
        GameObject rig = GameObject.Find("XR Interaction Setup");
        if (rig == null) rig = GameObject.Find("XR Origin (XR Rig)");
        if (rig == null)
        {
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(
                "Packages/com.xreal.xr/Runtime/Prefabs/XR Interaction Setup.prefab");
            if (prefab == null)
            {
                EditorUtility.DisplayDialog("Kiwi Sorter",
                    "XR Interaction Setup prefab not found. Is the XREAL SDK imported?", "OK");
                return;
            }
            rig = (GameObject)PrefabUtility.InstantiatePrefab(prefab);
            rig.name = "XR Interaction Setup";
        }

        Camera xrCam = rig.GetComponentInChildren<Camera>();
        if (xrCam == null)
        {
            EditorUtility.DisplayDialog("Kiwi Sorter",
                "Could not find the XR camera inside the rig.", "OK");
            return;
        }

        // 2. Black background on the XR camera (black = transparent on optical glasses)
        xrCam.clearFlags = CameraClearFlags.SolidColor;
        xrCam.backgroundColor = Color.black;

        // 3. Delete the plain default Main Camera (keep only the XR camera)
        foreach (var cam in Object.FindObjectsOfType<Camera>())
        {
            if (cam != xrCam && cam.transform.root.gameObject != rig &&
                cam.gameObject.name == "Main Camera")
                Object.DestroyImmediate(cam.gameObject);
        }

        // 4. Keep Canvas as Screen Space Overlay — renders to phone screen, which
        //    MRSpace displays as the app's floating panel in the glasses.
        //    Screen Space - Camera is ignored in MRSpace panel mode.
        var canvas = Object.FindObjectOfType<Canvas>();
        if (canvas != null)
        {
            canvas.renderMode  = RenderMode.ScreenSpaceOverlay;
            canvas.worldCamera = null;
        }

        // 5. Hide the camera-feed background (optical see-through = real world visible).
        //    The phone camera keeps capturing for the server; we just stop displaying it.
        var bg = GameObject.Find("CameraBackground");
        if (bg != null) bg.SetActive(false);

        MarkDirty();
        EditorUtility.DisplayDialog("Kiwi Sorter",
            "Glasses optical-AR mode set up:\n" +
            "- XREAL rig added\n- Camera feed hidden (see real world)\n" +
            "- Overlays render through XR camera\n\nSave (Ctrl+S), then Build.", "OK");
    }

    [MenuItem("KiwiSorter/Rebuild FruitPanel Prefab")]
    public static void RebuildPrefabOnly()
    {
        var prefab = BuildFruitPanelPrefab();

        // Reassign to the existing manager in the scene
        var mgr = Object.FindObjectOfType<FruitDetectionManager>();
        if (mgr != null)
        {
            mgr.fruitPanelPrefab = prefab;
            EditorUtility.SetDirty(mgr);
            MarkDirty();
        }
        EditorUtility.DisplayDialog("Kiwi Sorter",
            "FruitPanel prefab rebuilt with bounding box.\nSave (Ctrl+S), then Build.", "OK");
    }

    // ── Helpers ─────────────────────────────────────────────────────────────

    static void MarkDirty()
    {
        UnityEditor.SceneManagement.EditorSceneManager.MarkSceneDirty(
            UnityEditor.SceneManagement.EditorSceneManager.GetActiveScene());
    }

    static void StretchFull(RectTransform rt)
    {
        rt.anchorMin = Vector2.zero; rt.anchorMax = Vector2.one;
        rt.offsetMin = Vector2.zero; rt.offsetMax = Vector2.zero;
    }

    static Image MakeEdge(string name, Transform parent,
                          Vector2 aMin, Vector2 aMax, Vector2 pivot, Vector2 size)
    {
        var go = new GameObject(name, typeof(Image));
        go.transform.SetParent(parent, false);
        var rt = go.GetComponent<RectTransform>();
        rt.anchorMin = aMin; rt.anchorMax = aMax; rt.pivot = pivot;
        rt.sizeDelta = size; rt.anchoredPosition = Vector2.zero;
        go.GetComponent<Image>().color = new Color32(220, 48, 48, 255);
        return go.GetComponent<Image>();
    }

    static GameObject BuildFruitPanelPrefab()
    {
        // Root (sized to bbox at runtime; centre-anchored in PanelParent)
        var panel = new GameObject("FruitPanel", typeof(RectTransform));
        var prt = panel.GetComponent<RectTransform>();
        prt.anchorMin = new Vector2(0.5f, 0.5f);
        prt.anchorMax = new Vector2(0.5f, 0.5f);
        prt.pivot     = new Vector2(0.5f, 0.5f);
        prt.sizeDelta = new Vector2(200, 200);

        // Transparent fill
        var fillGO = new GameObject("BoxFill", typeof(Image));
        fillGO.transform.SetParent(panel.transform, false);
        StretchFull(fillGO.GetComponent<RectTransform>());
        var boxFill = fillGO.GetComponent<Image>();
        boxFill.color = new Color32(220, 48, 48, 28);
        boxFill.raycastTarget = false;

        // Outline edges (4 strips), thickness 5
        float th = 5f;
        var edgeTop    = MakeEdge("EdgeTop",    panel.transform, new Vector2(0,1), new Vector2(1,1), new Vector2(0.5f,1), new Vector2(0, th));
        var edgeBottom = MakeEdge("EdgeBottom", panel.transform, new Vector2(0,0), new Vector2(1,0), new Vector2(0.5f,0), new Vector2(0, th));
        var edgeLeft   = MakeEdge("EdgeLeft",   panel.transform, new Vector2(0,0), new Vector2(0,1), new Vector2(0,0.5f), new Vector2(th, 0));
        var edgeRight  = MakeEdge("EdgeRight",  panel.transform, new Vector2(1,0), new Vector2(1,1), new Vector2(1,0.5f), new Vector2(th, 0));

        // Label card — top-left of the box, sitting just above it.
        // Tall enough for FOUR non-overlapping rows; every text is clamped to its
        // row with ellipsis so a long defects string can never spill into the
        // quality section (the cause of the unreadable overlap on-glass).
        var cardGO = new GameObject("LabelCard", typeof(Image));
        cardGO.transform.SetParent(panel.transform, false);
        var cardRT = cardGO.GetComponent<RectTransform>();
        cardRT.anchorMin = new Vector2(0, 1);
        cardRT.anchorMax = new Vector2(0, 1);
        cardRT.pivot     = new Vector2(0, 0);          // bottom-left
        cardRT.sizeDelta = new Vector2(840, 440);
        cardRT.anchoredPosition = new Vector2(0, 8);   // just above the top edge
        var cardImg = cardGO.GetComponent<Image>();
        cardImg.color = new Color(0, 0, 0, 0.78f);
        cardImg.raycastTarget = false;

        // Row 1: fruit + confidence    Row 2: defects (max 2 lines, ellipsis)
        var label   = MakeText("LabelText",   cardGO.transform, 64, FontStyles.Bold,   new Vector2(26, -18),  new Vector2(790, 84), "FRUIT 0%");
        var size    = MakeText("SizeText",    cardGO.transform, 38, FontStyles.Normal, new Vector2(26, -112), new Vector2(790, 104), "Quality: analysing...");

        // Rows 3+4: quality grade | days left, then decay stage
        var qsec = new GameObject("QualitySection", typeof(RectTransform));
        qsec.transform.SetParent(cardGO.transform, false);
        var qrt = qsec.GetComponent<RectTransform>();
        qrt.anchorMin = new Vector2(0, 1); qrt.anchorMax = new Vector2(0, 1); qrt.pivot = new Vector2(0, 1);
        qrt.sizeDelta = new Vector2(790, 190); qrt.anchoredPosition = new Vector2(26, -236);

        var quality = MakeText("QualityText", qsec.transform, 58, FontStyles.Bold,   new Vector2(0, 0),     new Vector2(430, 76), "Good");
        var days    = MakeText("DaysText",    qsec.transform, 58, FontStyles.Bold,   new Vector2(450, 0),   new Vector2(340, 76), "0d left");
        var decay   = MakeText("DecayText",   qsec.transform, 42, FontStyles.Normal, new Vector2(0, -92),   new Vector2(790, 64), "Fresh");

        // Wire script
        var fp = panel.AddComponent<FruitPanel>();
        fp.boxFill = boxFill;
        fp.edgeTop = edgeTop; fp.edgeBottom = edgeBottom; fp.edgeLeft = edgeLeft; fp.edgeRight = edgeRight;
        fp.labelCard = cardRT;
        fp.labelText = label; fp.sizeText = size;
        fp.qualitySection = qsec;
        fp.qualityText = quality; fp.decayText = decay; fp.daysText = days;

        // Save prefab (overwrites existing — manager keeps its reference)
        if (!Directory.Exists("Assets/Prefabs")) Directory.CreateDirectory("Assets/Prefabs");
        var prefab = PrefabUtility.SaveAsPrefabAsset(panel, PrefabPath);
        Object.DestroyImmediate(panel);
        AssetDatabase.SaveAssets();
        return prefab;
    }

    static TextMeshProUGUI MakeText(string name, Transform parent, float size,
                                    FontStyles style, Vector2 pos, Vector2 dim, string txt)
    {
        var go = new GameObject(name, typeof(RectTransform));
        go.transform.SetParent(parent, false);
        var rt = go.GetComponent<RectTransform>();
        rt.anchorMin = new Vector2(0, 1); rt.anchorMax = new Vector2(0, 1); rt.pivot = new Vector2(0, 1);
        rt.sizeDelta = dim; rt.anchoredPosition = pos;
        var t = go.AddComponent<TextMeshProUGUI>();
        t.text = txt; t.fontSize = size; t.fontStyle = style;
        t.color = Color.white; t.alignment = TextAlignmentOptions.TopLeft;
        t.raycastTarget = false;
        // Clamp to the row: wrap, then cut with "…" — text can NEVER overflow
        // its rect into the row below (the overlap bug).
        t.enableWordWrapping = true;
        t.overflowMode = TextOverflowModes.Ellipsis;
        return t;
    }
}
