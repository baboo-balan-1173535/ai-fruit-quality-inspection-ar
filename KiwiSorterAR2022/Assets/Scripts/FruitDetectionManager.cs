// FruitDetectionManager.cs — Main AR detection controller (Milestone 1: phone screen).
//
// Dual pipeline:
//   Fast  -> POST /detect     every 500ms  (YOLO only)     updates panel positions
//   Deep  -> POST /ar-analyze every 3s     (YOLO + Claude)  updates quality info
//
// Includes:
//   - Android camera permission request
//   - On-screen status overlay (OnGUI) so you can debug without adb
//   - Robust camera startup

using System.Collections;
using System.Collections.Generic;
using System.Net;
using System.Net.Sockets;
using System.Text;
using UnityEngine;
using UnityEngine.Networking;
using UnityEngine.UI;
using UnityEngine.XR;
using UnityEngine.XR.Management;
using Unity.XR.XREAL;
#if UNITY_ANDROID
using UnityEngine.Android;
#endif

public class FruitDetectionManager : MonoBehaviour
{
    [Header("Flask Server - must match .env")]
    [Tooltip("FALLBACK only. The app auto-discovers the server via UDP broadcast\n" +
             "(port 5006) on whatever WiFi it is on, so this works on any network\n" +
             "without a rebuild. This value is used until discovery succeeds.")]
    public string serverIP   = "192.168.68.59";
    public int    serverPort = 5000;
    public string arToken    = "kiwi-ar-2026";

    [Header("Camera source")]
    [Tooltip("ON = XReal Eye (head viewpoint, optical AR). OFF = phone camera (mirror mode).")]
    public bool useEyeCamera = true;

    [Header("Pipeline timing (seconds)")]
    public float detectInterval  = 0.2f;
    public float analyzeInterval = 3.0f;
    [Tooltip("ON = Claude /ar-analyze pipeline (quality/decay/shelf-life on the panel).\n" +
             "Server has a lightweight AR path now (no GrabCut, one Claude call for the\n" +
             "largest fruit), so it no longer stalls detection. Turn OFF while calibrating.")]
    public bool enableAnalyze = true;

    [Header("Optical mapping (world-anchored)")]
    [Tooltip("XReal Eye camera HORIZONTAL field of view in degrees. This converts a\n" +
             "detection's image position into a real-world direction. Tune so the box\n" +
             "SIZE and left/right position match the real apple. If the box moves the\n" +
             "WRONG way left/right, make this NEGATIVE (camera is mirrored).")]
    public float eyeCameraFovH = 96f;
    [Tooltip("XReal Eye camera VERTICAL field of view in degrees. Tune so the box's\n" +
             "up/down position matches the real apple. Negative flips vertical.")]
    public float eyeCameraFovV = 52f;
    [Tooltip("Distance (metres) to place the world anchor in front of you. Only matters\n" +
             "for parallax; 1.5–2.5 is fine for an apple at arm's length.")]
    public float anchorDistance = 2f;
    [Tooltip("Fixed angular offset (degrees) for the Eye-camera-vs-eye mounting gap.\n" +
             "X: right(+)/left(−), Y: up(+)/down(−). Nudge so a centred apple's box is\n" +
             "exactly on it.")]
    public Vector2 aimOffsetDeg = Vector2.zero;
    [Tooltip("Box SIZE multiplier, independent of position. Tune the FOV first for\n" +
             "POSITION (box lands on off-centre apples), then use this so the box fully\n" +
             "COVERS the apple. 1.0 = raw.")]
    public float boxSizeScale = 1.20f;
    [Tooltip("Keep a box on screen this many seconds after detection briefly drops, so\n" +
             "flickery / low-confidence detection doesn't make the box blink off. It stays\n" +
             "world-anchored on the apple during the gap. 0 = clear immediately.")]
    public float trackGraceSeconds = 1.5f;

    [Header("Motion smoothing")]
    [Tooltip("How fast the box glides toward each new detection (per second, exponential).\n" +
             "Detections arrive ~5x/sec and used to SNAP the box each time = rough motion.\n" +
             "Now each detection is a TARGET the box eases toward every frame.\n" +
             "8-15 = smooth but responsive. Higher = snappier, lower = floatier.")]
    public float posSmoothing = 10f;
    [Tooltip("Same idea for the box SIZE. Slightly slower than position looks best.")]
    public float sizeSmoothing = 8f;
    [Tooltip("CATCH-UP boost: when the box is far behind the target (fruit or head\n" +
             "moved fast), smoothing speeds up by up to this multiple so it closes the\n" +
             "gap quickly, then settles back to gentle gliding near the target.\n" +
             "0 = constant smoothing. 3 = up to 4x faster when >30cm behind.")]
    public float catchUpBoost = 3f;

    [Header("Scene references")]
    public RawImage      cameraBackground;
    public RectTransform panelParent;
    public GameObject    fruitPanelPrefab;

    // ── XReal Eye camera ──
    XREALRGBCameraTexture _eyeTex;
    Material      _yuvMat;
    RenderTexture _eyeRT;
    Texture2D     _eyeReadTex;
    bool          _eyeReady;
    Camera        _xrCam;          // XR head camera, for gyro reprojection

    // Private
    WebCamTexture _webcam;
    float  _detectTimer, _analyzeTimer;
    float  _captureLostTimer;     // how long the Eye camera has reported no capture
    float  _lastDetectTime;       // last time a detection returned >=1 fruit (for grace period)
    bool   _detecting;
    string _status = "Starting...";
    string _lastServerMsg = "";
    string _deviceInfo = "";
    string _chosenCam = "";
    string _camRes = "";
    int    _camIndex = -1;   // -1 = pick a back camera on first run
    int    _fruitCount = 0;
    int    _frameW = 1280, _frameH = 720;  // source frame dimensions for letterbox mapping
    readonly List<FruitPanel> _panels = new List<FruitPanel>();

    void Start()
    {
        // Our overlay is display-only (boxes + IMGUI CAL panel) — it must never
        // swallow pointer raycasts meant for SDK UI like the XREAL quit dialog.
        if (panelParent != null)
        {
            var canvas = panelParent.GetComponentInParent<Canvas>();
            if (canvas != null)
            {
                var gr = canvas.GetComponent<UnityEngine.UI.GraphicRaycaster>();
                if (gr != null) gr.enabled = false;
            }
        }

        StartCoroutine(InitRoutine());
        StartCoroutine(DiscoverServer());
    }

    // ── Server auto-discovery (UDP broadcast) ───────────────────────────────
    // Broadcasts a probe on the local network; the Flask server answers with
    // its current IP. Removes the hardcoded-IP problem: the app finds the
    // server on ANY WiFi. Falls back to the serialized serverIP until a reply
    // arrives, and keeps re-probing slowly so a network change self-heals.
    [System.Serializable] class DiscoveryReply { public string app; public string ip; public int port; }

    bool _serverDiscovered;

    IEnumerator DiscoverServer()
    {
        UdpClient udp = null;
        try
        {
            udp = new UdpClient();
            udp.EnableBroadcast = true;
            udp.Client.Blocking = false;
        }
        catch (System.Exception e)
        {
            _lastServerMsg = $"Discovery unavailable: {e.Message}";
            yield break;
        }

        byte[] probe = Encoding.UTF8.GetBytes("KIWI_SORTER_DISCOVERY_V1");
        var broadcast = new IPEndPoint(IPAddress.Broadcast, 5006);
        var anyEp     = new IPEndPoint(IPAddress.Any, 0);

        while (true)
        {
            try { udp.Send(probe, probe.Length, broadcast); } catch { }

            float waited = 0f;
            while (waited < 1.5f)
            {
                if (udp.Available > 0)
                {
                    try
                    {
                        byte[] resp = udp.Receive(ref anyEp);
                        var m = JsonUtility.FromJson<DiscoveryReply>(Encoding.UTF8.GetString(resp));
                        if (m != null && m.app == "kiwi-sorter" && !string.IsNullOrEmpty(m.ip))
                        {
                            if (serverIP != m.ip)
                            {
                                serverIP = m.ip;
                                if (m.port > 0) serverPort = m.port;
                                _lastServerMsg = $"Server discovered: {serverIP}:{serverPort}";
                            }
                            _serverDiscovered = true;
                        }
                    }
                    catch { }
                    break;
                }
                yield return new WaitForSeconds(0.1f);
                waited += 0.1f;
            }

            // Found: re-check occasionally (handles moving to another WiFi).
            // Not found yet: keep probing quickly.
            yield return new WaitForSeconds(_serverDiscovered ? 10f : 2f);
        }
    }

    IEnumerator InitRoutine()
    {
        _status = "Requesting camera permission...";
#if UNITY_ANDROID
        if (!Permission.HasUserAuthorizedPermission(Permission.Camera))
        {
            Permission.RequestUserPermission(Permission.Camera);
            float waited = 0f;
            while (!Permission.HasUserAuthorizedPermission(Permission.Camera) && waited < 30f)
            {
                yield return new WaitForSeconds(0.5f);
                waited += 0.5f;
            }
        }
#endif
        if (useEyeCamera)
            yield return StartCoroutine(StartEyeCamera());
        else
        {
            // Mirror mode only: lock landscape + show camera feed
            Screen.orientation = ScreenOrientation.LandscapeLeft;
            yield return StartCoroutine(StartPhoneCamera());
        }
    }

    // ── XReal Eye camera (optical AR) ───────────────────────────────────────
    IEnumerator StartEyeCamera()
    {
        _status = "Starting XReal Eye camera...";
        _chosenCam = "XReal Eye (SDK)";
        _deviceInfo = "";

        // OPTION B (optical AR): render the box THROUGH the XR camera so it appears
        // in 3D/immersive mode (the mode we want). ScreenSpaceOverlay was wrong — it
        // only shows on the flat 2D screen, forcing a switch to the 2D MRSpace panel.
        // Screen Space - Camera keeps all the existing pixel-space bbox math intact
        // but composites through the XR stereo camera, so black = transparent and the
        // box floats in the field of view over the real world.
        var canvas = panelParent != null
            ? panelParent.GetComponentInParent<Canvas>()
            : FindObjectOfType<Canvas>();

        Camera xrCam = Camera.main;
        if (xrCam == null)
        {
            var rig = GameObject.Find("XR Origin (XR Rig)");
            if (rig == null) rig = GameObject.Find("XR Interaction Setup");
            if (rig != null) xrCam = rig.GetComponentInChildren<Camera>();
        }

        _xrCam = xrCam;   // cache for head-rotation reprojection

        if (canvas != null && xrCam != null)
        {
            canvas.renderMode   = RenderMode.ScreenSpaceCamera;
            canvas.worldCamera  = xrCam;
            canvas.planeDistance = 2f;   // metres in front of the eye
            _status = "Eye + XR camera render (3D mode). Keep glasses in 3D.";
        }
        else if (canvas != null)
        {
            // No XR camera found — fall back to overlay so at least 2D mode shows it.
            canvas.renderMode  = RenderMode.ScreenSpaceOverlay;
            canvas.worldCamera = null;
            _status = "No XR camera found — using 2D overlay fallback.";
        }

        // Load from Resources (guaranteed in build); fall back to Shader.Find
        var sh = Resources.Load<Shader>("EyeYUVtoRGB");
        if (sh == null) sh = Shader.Find("KiwiSorter/EyeYUVtoRGB");
        if (sh == null) { _status = "EyeYUVtoRGB shader missing!"; yield break; }
        _yuvMat = new Material(sh);

        _eyeTex = XREALRGBCameraTexture.CreateSingleton();
        _eyeTex.StartCapture();

        // Wait for the first frame (valid resolution)
        float t = 0f;
        while (t < 8f)
        {
            var res = _eyeTex.GetResolution();
            var yuv = _eyeTex.GetYUVFormatTextures();
            if (_eyeTex.IsCapturing && res.x > 16 && yuv[0] != null)
            {
                _camRes  = $"{res.x}x{res.y}";
                _frameW  = res.x;
                _frameH  = res.y;
                _eyeReady = true;
                _status = "Eye camera running. Look at fruit.";
                Debug.Log($"[AR] Eye camera {res.x}x{res.y}");
                yield break;
            }
            yield return new WaitForSeconds(0.2f);
            t += 0.2f;
        }
        _status = "Eye camera no frames.\nIs UVC/Camera enabled in ControlGlasses?";
    }

    byte[] CaptureEyeJpeg(int quality)
    {
        var yuv = _eyeTex.GetYUVFormatTextures();
        if (yuv[0] == null) return null;
        var res = _eyeTex.GetResolution();
        int w = res.x, h = res.y;
        if (w <= 16) return null;

        // Full-res RT for YUV→RGB conversion
        if (_eyeRT == null || _eyeRT.width != w || _eyeRT.height != h)
        {
            if (_eyeRT != null) _eyeRT.Release();
            _eyeRT = new RenderTexture(w, h, 0, RenderTextureFormat.ARGB32);
        }
        _yuvMat.SetTexture("_MainTex", yuv[0]);
        _yuvMat.SetTexture("_UTex",    yuv[1]);
        _yuvMat.SetTexture("_VTex",    yuv[2]);
        Graphics.Blit(yuv[0], _eyeRT, _yuvMat);

        // Scale down to 640×360 before ReadPixels — 4× fewer pixels = ~4× faster
        // stall + 4× faster JPEG encode. Aspect ratio is identical (16:9) so
        // bbox_norm values from YOLO are unchanged.
        const int sw = 640, sh = 360;
        var scaledRT = RenderTexture.GetTemporary(sw, sh, 0, RenderTextureFormat.ARGB32);
        Graphics.Blit(_eyeRT, scaledRT);

        if (_eyeReadTex == null || _eyeReadTex.width != sw || _eyeReadTex.height != sh)
        {
            if (_eyeReadTex != null) Destroy(_eyeReadTex);
            _eyeReadTex = new Texture2D(sw, sh, TextureFormat.RGB24, false);
        }

        var prev = RenderTexture.active;
        RenderTexture.active = scaledRT;
        _eyeReadTex.ReadPixels(new Rect(0, 0, sw, sh), 0, 0);
        _eyeReadTex.Apply();
        RenderTexture.active = prev;
        RenderTexture.ReleaseTemporary(scaledRT);

        return _eyeReadTex.EncodeToJPG(quality);
    }

    IEnumerator StartPhoneCamera()
    {
        _status = "Looking for camera...";
        yield return new WaitForSeconds(0.3f);

        WebCamDevice[] devices = WebCamTexture.devices;
        if (devices.Length == 0)
        {
            _status = "No camera devices found.";
            yield break;
        }

        // Report all available cameras with their index
        _deviceInfo = "Cams: ";
        for (int i = 0; i < devices.Length; i++)
            _deviceInfo += $"[{i}]{(devices[i].isFrontFacing ? "F" : "B")} ";

        // First run: prefer a back camera. On cycle: wrap around all devices.
        if (_camIndex < 0)
        {
            _camIndex = 0;
            for (int i = 0; i < devices.Length; i++)
                if (!devices[i].isFrontFacing) { _camIndex = i; break; }
        }
        else
        {
            _camIndex = ((_camIndex % devices.Length) + devices.Length) % devices.Length;
        }

        string camName = devices[_camIndex].name;
        _chosenCam = $"cam[{_camIndex}] {camName} ({(devices[_camIndex].isFrontFacing ? "F" : "B")})";

        if (_webcam != null) { _webcam.Stop(); _webcam = null; }
        _webcam = new WebCamTexture(camName, 1280, 720, 30);
        _webcam.Play();

        float t = 0f;
        while (_webcam.width <= 16 && t < 5f)
        {
            yield return new WaitForSeconds(0.1f);
            t += 0.1f;
        }

        if (_webcam.width <= 16)
        {
            _camRes = "no frames";
            _status = $"cam[{_camIndex}] black/no frames. TAP to try next.";
            yield break;
        }

        _camRes  = $"{_webcam.width}x{_webcam.height} rot{_webcam.videoRotationAngle}";
        _frameW  = _webcam.width;
        _frameH  = _webcam.height;

        if (cameraBackground != null)
        {
            cameraBackground.texture = _webcam;
            // Full-stretch so the feed always fills the screen (no zero-size fitter issues)
            var rt = cameraBackground.rectTransform;
            rt.anchorMin = Vector2.zero;
            rt.anchorMax = Vector2.one;
            rt.pivot     = new Vector2(0.5f, 0.5f);
            rt.offsetMin = Vector2.zero;
            rt.offsetMax = Vector2.zero;
            rt.localEulerAngles = new Vector3(0, 0, -_webcam.videoRotationAngle);
            var fit = cameraBackground.GetComponent<AspectRatioFitter>();
            if (fit != null) fit.enabled = false;
        }

        _status = "Camera running. TAP screen to switch camera.";
        Debug.Log($"[AR] Camera[{_camIndex}] {camName} {_webcam.width}x{_webcam.height}");
    }

    bool CameraReady()
    {
        if (useEyeCamera) return _eyeReady && _eyeTex != null && _eyeTex.IsCapturing;
        return _webcam != null && _webcam.isPlaying;
    }

    void Update()
    {
        // Android BACK button (phone) = quit.
        if (Input.GetKeyDown(KeyCode.Escape)) Application.Quit();

        // HOME button = quit directly. The SDK's home press shows a Cancel/Quit
        // dialog that can't receive taps in our immersive app (no SDK pointer —
        // Goal 6 territory), so the moment that dialog appears we close it and
        // quit, making the home button a one-press exit.
        var homeMenu = XREALHomeMenu.Singleton;
        if (homeMenu != null && homeMenu.gameObject.activeSelf)
        {
            homeMenu.Show(false);
            XREALPlugin.QuitApplication();
        }

        // Phone/mirror mode: tap to cycle cameras. (Eye mode has a single source.)
        if (!useEyeCamera &&
            (Input.GetMouseButtonDown(0) ||
             (Input.touchCount > 0 && Input.GetTouch(0).phase == TouchPhase.Began)))
        {
            _camIndex++;
            StartCoroutine(StartPhoneCamera());
            return;
        }

        // Eye camera watchdog — restart only if capture is lost for a SUSTAINED period.
        // (A momentary IsCapturing=false between frames must NOT trigger a full restart,
        // or detection drops out constantly.)
        if (useEyeCamera && _eyeReady && _eyeTex != null)
        {
            if (!_eyeTex.IsCapturing) _captureLostTimer += Time.deltaTime;
            else                      _captureLostTimer = 0f;

            if (_captureLostTimer > 2.0f)
            {
                Debug.Log("[AR] Eye camera lost >2s — restarting...");
                _captureLostTimer = 0f;
                _eyeReady = false;
                _status = "Eye camera reconnecting...";
                StartCoroutine(StartEyeCamera());
                return;
            }
        }

        if (!CameraReady()) return;

        _detectTimer  += Time.deltaTime;
        _analyzeTimer += Time.deltaTime;

        // ACQUISITION MODE: while nothing is tracked yet, poll as fast as the
        // round-trip allows (requests are serialized by _detecting anyway) so the
        // FIRST box appears quickly. Once tracking, drop to the normal interval.
        float interval = _tracks.Count == 0 ? 0.05f : detectInterval;

        if (_detectTimer >= interval && !_detecting)
        {
            _detectTimer = 0f;
            StartCoroutine(RunDetect());
        }
        // Analyze only AFTER something is tracked: the 1-2s Claude round trip has
        // nothing to analyse on an empty frame and only competes with acquisition.
        if (enableAnalyze && _tracks.Count > 0 && _analyzeTimer >= analyzeInterval)
        {
            _analyzeTimer = 0f;
            StartCoroutine(RunAnalyze());
        }

        FruitPanel.AnalyzeOn = enableAnalyze;   // keeps the panel's placeholder honest

        // Reposition boxes every frame (world-anchored projection). Guarded so a
        // single render hiccup can never break the detection scheduling above.
        try { RenderTracks(); }
        catch (System.Exception e) { Debug.LogError($"[AR] RenderTracks: {e}"); }
    }

    void OnDestroy()
    {
        if (_webcam != null) _webcam.Stop();
        if (_eyeTex != null && _eyeTex.IsCapturing) _eyeTex.StopCapture();
        if (_eyeRT != null) _eyeRT.Release();
    }

    byte[] CaptureJpeg(int quality)
    {
        if (useEyeCamera) return CaptureEyeJpeg(quality);

        var tex = new Texture2D(_webcam.width, _webcam.height, TextureFormat.RGB24, false);
        tex.SetPixels(_webcam.GetPixels());
        tex.Apply();
        var jpg = tex.EncodeToJPG(quality);
        Destroy(tex);
        return jpg;
    }

    string DetectUrl   => $"http://{serverIP}:{serverPort}/detect";
    string AnalyzeUrl  => $"http://{serverIP}:{serverPort}/ar-analyze";
    string SnapshotUrl => $"http://{serverIP}:{serverPort}/ar-snapshot";
    string RecordUrl   => $"http://{serverIP}:{serverPort}/ar-record";

    // ── Screen recording (server assembles MP4 from the /detect frames) ─────
    bool _recording, _recBusy;

    [System.Serializable] class RecReply { public bool recording; public string url; public int frames; }

    IEnumerator ToggleRecord()
    {
        _recBusy = true;
        var req = new UnityWebRequest(RecordUrl, "POST");
        byte[] body = Encoding.UTF8.GetBytes("{\"action\":\"toggle\"}");
        req.uploadHandler   = new UploadHandlerRaw(body);
        req.downloadHandler = new DownloadHandlerBuffer();
        req.SetRequestHeader("Content-Type", "application/json");
        req.SetRequestHeader("X-AR-Token", arToken);
        req.timeout = 6;
        yield return req.SendWebRequest();
        if (req.result == UnityWebRequest.Result.Success)
        {
            var rep = JsonUtility.FromJson<RecReply>(req.downloadHandler.text);
            _recording = rep != null && rep.recording;
            _snapMsg = _recording
                ? "Recording... (REC again to stop)"
                : (rep != null && !string.IsNullOrEmpty(rep.url)
                    ? $"Recording saved on PC ({rep.frames} frames):\n{rep.url}"
                    : "Recording stopped");
        }
        else _snapMsg = $"Record failed: {req.error}";
        req.Dispose();
        _snapMsgUntil = Time.time + 6f;
        _recBusy = false;
    }

    // ── Snapshot ("what I see" → JPEG on the server) ────────────────────────
    bool   _snapping;
    string _snapMsg = "";
    float  _snapMsgUntil;

    [System.Serializable] class SnapReply { public bool saved; public string url; public string error; }

    IEnumerator TakeSnapshot()
    {
        _snapping = true;
        byte[] jpg = CaptureJpeg(90);   // best quality for sharing
        if (jpg == null)
        {
            _snapMsg = "Snapshot failed: no camera frame";
            _snapMsgUntil = Time.time + 4f;
            _snapping = false;
            yield break;
        }

        WWWForm form = new WWWForm();
        form.AddBinaryData("image", jpg, "snap.jpg", "image/jpeg");
        // Pass the current quality info so the server prints it on the image
        if (_tracks.Count > 0 && _tracks[0] != null && _tracks[0].qa != null)
        {
            var qa = _tracks[0].qa;
            form.AddField("info",
                $"Quality: {qa.quality} | Decay: {qa.decay_stage} | {qa.days_remaining}d left");
        }
        // Pass the boxes the glasses are showing RIGHT NOW so the snapshot
        // matches what the user sees (a fresh server detection can miss).
        var sb = new StringBuilder();
        foreach (var tr in _tracks)
        {
            if (tr == null || tr.det == null || tr.det.bbox_norm == null || tr.det.bbox_norm.Length < 4) continue;
            if (sb.Length > 0) sb.Append(';');
            sb.Append($"{tr.det.label},{tr.det.confidence:F0}," +
                      $"{tr.det.bbox_norm[0]:F4},{tr.det.bbox_norm[1]:F4}," +
                      $"{tr.det.bbox_norm[2]:F4},{tr.det.bbox_norm[3]:F4}");
        }
        if (sb.Length > 0) form.AddField("boxes", sb.ToString());

        using (var req = UnityWebRequest.Post(SnapshotUrl, form))
        {
            req.SetRequestHeader("X-AR-Token", arToken);
            req.timeout = 8;
            yield return req.SendWebRequest();
            if (req.result == UnityWebRequest.Result.Success)
            {
                var rep = JsonUtility.FromJson<SnapReply>(req.downloadHandler.text);
                _snapMsg = rep != null && rep.saved
                    ? $"Snapshot saved on PC:\n{rep.url}"
                    : "Snapshot failed: bad reply";
            }
            else _snapMsg = $"Snapshot failed: {req.error}";
        }
        _snapMsgUntil = Time.time + 6f;
        _snapping = false;
    }

    IEnumerator RunDetect()
    {
        _detecting = true;
        byte[] jpg = CaptureJpeg(50);   // 640×360 q50 = tiny payload, sufficient for YOLO
        if (jpg == null) { _detecting = false; yield break; }
        WWWForm form = new WWWForm();
        form.AddBinaryData("image", jpg, "frame.jpg", "image/jpeg");

        bool ok = false; string body = null, err = null;
        using (var req = UnityWebRequest.Post(DetectUrl, form))
        {
            req.SetRequestHeader("X-AR-Token", arToken);
            req.timeout = 4;   // a hung request must not block the loop for long
            yield return req.SendWebRequest();
            ok = req.result == UnityWebRequest.Result.Success;
            if (ok) body = req.downloadHandler.text; else err = req.error;
        }

        _detecting = false;   // ALWAYS reset before parsing, so one bad frame can't wedge the pipeline

        if (!ok)
        {
            _lastServerMsg = $"detect FAIL: {err}";
            _status = $"Cannot reach server at {serverIP}:{serverPort}\n{err}";
            yield break;
        }

        try
        {
            var resp = JsonUtility.FromJson<DetectResponse>(body);
            if (resp != null && resp.detections != null)
            {
                _fruitCount = resp.detections.Length;
                RefreshPanels(resp.detections, null);
                _lastServerMsg = $"detect OK ({_fruitCount} fruit)";
            }
        }
        catch (System.Exception e)
        {
            _lastServerMsg = "detect handler error (recovered)";
            Debug.LogError($"[AR] detect handler: {e}");
        }
    }

    IEnumerator RunAnalyze()
    {
        byte[] jpg = CaptureJpeg(85);
        if (jpg == null) yield break;
        WWWForm form = new WWWForm();
        form.AddBinaryData("image", jpg, "frame.jpg", "image/jpeg");

        bool ok = false; string body = null, err = null;
        using (var req = UnityWebRequest.Post(AnalyzeUrl, form))
        {
            req.SetRequestHeader("X-AR-Token", arToken);
            req.timeout = 15;
            yield return req.SendWebRequest();
            ok = req.result == UnityWebRequest.Result.Success;
            if (ok) body = req.downloadHandler.text; else err = req.error;
        }

        if (!ok) { _lastServerMsg = $"analyze FAIL: {err}"; yield break; }

        try
        {
            var resp = JsonUtility.FromJson<AnalyzeResponse>(body);
            if (resp != null && resp.detections != null)
            {
                RefreshPanels(resp.detections, resp.analysis);
                _lastServerMsg = $"analyze OK ({resp.detections.Length} fruit + Claude)";
            }
        }
        catch (System.Exception e)
        {
            _lastServerMsg = "analyze handler error (recovered)";
            Debug.LogError($"[AR] analyze handler: {e}");
        }
    }

    // ── World-anchored tracking ─────────────────────────────────────────────
    // A flat screen-space canvas can't map to the landscape optical FOV (the canvas
    // is portrait phone-shaped). Instead we turn each detection into a real-world
    // POINT (direction from the head + a fixed distance), then let the XR camera
    // PROJECT it to the screen every frame. Unity handles FOV/aspect correctly, and
    // because the point is world-fixed it stays on the apple as the head moves.
    class Track
    {
        public Vector3 world;        // DISPLAYED world position (smoothed toward worldTarget)
        public Vector3 worldTarget;  // latest detection's anchored position
        public float   halfW, halfH;             // displayed half-extents (smoothed)
        public float   halfWTarget, halfHTarget; // latest detection's half-extents
        public Vector2 imgCenter;    // detected image centre (for matching + debug)
        public DetectionData det;    // for label/size/colour content
        public AnalysisData  qa;     // Claude quality (may be null)
        public int labelFlips;       // consecutive detections disagreeing with the shown label
    }
    readonly List<Track> _tracks = new List<Track>();
    const float MatchWorldDist = 0.5f;   // metres: same fruit if anchors are this close

    // Live diagnostics (shown in the tap-to-show debug overlay)
    Vector2 _dbgCenter;   // raw DETECTED centre of track 0 (image-normalised, 0..1)
    Vector2 _dbgPxNorm;   // RENDERED centre of track 0 (fraction of canvas, 0..1)
    Vector2 _dbgCanvas;   // canvas pw, ph in px
    int     _dbgTracks;   // current track count

    static Vector2 CenterOf(float[] b) => new Vector2((b[0] + b[2]) * 0.5f, (b[1] + b[3]) * 0.5f);
    static Vector2 SizeOf(float[] b)   => new Vector2(Mathf.Abs(b[2] - b[0]), Mathf.Abs(b[3] - b[1]));

    // Turn a detection (image-normalised centre + size) into a world anchor using the
    // current head pose and the Eye camera FOV. Direction only — distance is fixed.
    bool DetectionToWorld(Vector2 c, Vector2 sz, out Vector3 world, out float halfW, out float halfH)
    {
        world = Vector3.zero; halfW = halfH = 0f;
        if (_xrCam == null) return false;

        float angX = (c.x - 0.5f) * eyeCameraFovH + aimOffsetDeg.x;   // right +
        float angY = (0.5f - c.y) * eyeCameraFovV + aimOffsetDeg.y;   // up +  (image y is top-down)

        Vector3 localDir = Quaternion.Euler(-angY, angX, 0f) * Vector3.forward;
        Vector3 worldDir = _xrCam.transform.rotation * localDir;
        world = _xrCam.transform.position + worldDir * Mathf.Max(0.2f, anchorDistance);

        float ss = Mathf.Max(0.05f, boxSizeScale);
        halfW = anchorDistance * Mathf.Tan(0.5f * sz.x * Mathf.Abs(eyeCameraFovH) * Mathf.Deg2Rad) * ss;
        halfH = anchorDistance * Mathf.Tan(0.5f * sz.y * Mathf.Abs(eyeCameraFovV) * Mathf.Deg2Rad) * ss;
        return true;
    }

    // Fresh detection: reconcile panels + re-anchor each fruit in world space.
    void RefreshPanels(DetectionData[] dets, AnalysisData[] analyses)
    {
        int needed = dets != null ? dets.Length : 0;

        // Grace period: a brief EMPTY detection (low-confidence flicker) must NOT clear
        // the box. Keep the existing world-anchored boxes until detection has been gone
        // for trackGraceSeconds — only then let it fall through and clear.
        if (needed == 0)
        {
            if (Time.time - _lastDetectTime < trackGraceSeconds) return;
        }
        else _lastDetectTime = Time.time;

        // PANEL POOL — never Destroy. With a bag of apples the detection count
        // flips every cycle (0->3->1->4...) and Destroy/Instantiate churn caused
        // GC spikes that froze the app progressively (the 25s stall in the server
        // log). Panels are created once up to a cap and just toggled active.
        const int MaxPanels = 6;
        if (needed > MaxPanels) needed = MaxPanels;
        while (_panels.Count < needed)
        {
            var go = Instantiate(fruitPanelPrefab, panelParent);
            _panels.Add(go.GetComponent<FruitPanel>());
        }
        for (int p = needed; p < _panels.Count; p++)
            if (_panels[p] != null) _panels[p].gameObject.SetActive(false);

        var newTracks = new List<Track>(needed);
        var used = new bool[_tracks.Count];

        for (int i = 0; i < needed; i++)
        {
            var d = dets[i];
            if (d.bbox_norm == null || d.bbox_norm.Length < 4) { newTracks.Add(null); continue; }

            Vector2 c  = CenterOf(d.bbox_norm);
            Vector2 sz = SizeOf(d.bbox_norm);
            if (!DetectionToWorld(c, sz, out Vector3 world, out float halfW, out float halfH))
            { newTracks.Add(null); continue; }

            var qa = (analyses != null && i < analyses.Length) ? analyses[i] : null;
            var t = new Track { world = world, worldTarget = world,
                                halfW = halfW, halfH = halfH,
                                halfWTarget = halfW, halfHTarget = halfH,
                                imgCenter = c, det = d, qa = qa };

            // Carry Claude quality across fast (detect-only) updates via nearest anchor.
            int best = -1; float bestDist = MatchWorldDist;
            for (int j = 0; j < _tracks.Count; j++)
            {
                if (used[j] || _tracks[j] == null) continue;
                float dist = Vector3.Distance(_tracks[j].worldTarget, world);
                if (dist < bestDist) { bestDist = dist; best = j; }
            }
            if (best >= 0)
            {
                used[best] = true;
                var old = _tracks[best];
                if (t.qa == null) t.qa = old.qa;

                // Motion smoothing: a MATCHED track keeps its currently displayed
                // position/size and only its TARGET moves to the new detection.
                // RenderTracks eases displayed -> target every frame, so the box
                // glides instead of snapping 5x a second.
                t.world = old.world;
                t.halfW = old.halfW;
                t.halfH = old.halfH;

                // Label smoothing: the stock COCO model flips apple<->orange on round
                // fruit. Keep the established label until the NEW label has persisted
                // for 3 consecutive detections — stops the box recolouring/relabelling
                // every few frames while still allowing a genuine correction through.
                if (old.det != null && old.det.label != d.label)
                {
                    t.labelFlips = old.labelFlips + 1;
                    if (t.labelFlips < 3) t.det.label = old.det.label;
                    else t.labelFlips = 0;   // accept the new label, reset
                }
            }
            newTracks.Add(t);
        }

        _tracks.Clear();
        _tracks.AddRange(newTracks);
    }

    // Every frame: project each world anchor to the screen via the XR camera. Unity's
    // projection handles FOV/aspect/portrait-canvas correctly, and the world-fixed
    // anchor keeps the box on the real apple as the head moves.
    void RenderTracks()
    {
        if (panelParent == null) return;
        _dbgCanvas = new Vector2(panelParent.rect.width, panelParent.rect.height);
        _dbgTracks = _tracks.Count;
        if (_xrCam == null) return;

        // Exponential smoothing factors (frame-rate independent)
        float kSize = 1f - Mathf.Exp(-Mathf.Max(0.1f, sizeSmoothing) * Time.deltaTime);

        for (int i = 0; i < _panels.Count && i < _tracks.Count; i++)
        {
            var t = _tracks[i];
            var panel = _panels[i];
            if (t == null) { panel.gameObject.SetActive(false); continue; }

            // Glide displayed position/size toward the latest detection target.
            // Catch-up: the further behind the target, the faster the glide — so
            // a moving fruit is chased quickly, while a stationary one stays calm.
            float dist  = Vector3.Distance(t.world, t.worldTarget);
            float boost = 1f + Mathf.Max(0f, catchUpBoost) * Mathf.Clamp01(dist / 0.3f);
            float kPos  = 1f - Mathf.Exp(-Mathf.Max(0.1f, posSmoothing) * boost * Time.deltaTime);
            t.world = Vector3.Lerp(t.world, t.worldTarget, kPos);
            t.halfW = Mathf.Lerp(t.halfW, t.halfWTarget, kSize);
            t.halfH = Mathf.Lerp(t.halfH, t.halfHTarget, kSize);

            Vector3 sp = _xrCam.WorldToScreenPoint(t.world);
            if (sp.z <= 0f) { panel.gameObject.SetActive(false); continue; } // behind the head
            panel.gameObject.SetActive(true);

            // Centre + two offset corners -> canvas-local points (Unity does the projection)
            Vector3 spR = _xrCam.WorldToScreenPoint(t.world + _xrCam.transform.right * t.halfW);
            Vector3 spU = _xrCam.WorldToScreenPoint(t.world + _xrCam.transform.up    * t.halfH);
            RectTransformUtility.ScreenPointToLocalPointInRectangle(panelParent, sp,  _xrCam, out Vector2 lpC);
            RectTransformUtility.ScreenPointToLocalPointInRectangle(panelParent, spR, _xrCam, out Vector2 lpR);
            RectTransformUtility.ScreenPointToLocalPointInRectangle(panelParent, spU, _xrCam, out Vector2 lpU);

            float halfWpx = Mathf.Max(6f, Mathf.Abs(lpR.x - lpC.x));
            float halfHpx = Mathf.Max(6f, Mathf.Abs(lpU.y - lpC.y));

            panel.UpdatePanel(t.det, t.qa, lpC, new Vector2(halfWpx * 2f, halfHpx * 2f));

            if (i == 0)
            {
                _dbgCenter = t.imgCenter;
                float pw = panelParent.rect.width, ph = panelParent.rect.height;
                _dbgPxNorm = new Vector2(pw > 1f ? sp.x / Screen.width : 0f,
                                         ph > 1f ? sp.y / Screen.height : 0f);
            }
        }
    }

    // Builds a one-line report on whether XR (immersive stereo) is actually
    // running on the device. If XR is NOT active, the app is a plain 2D Android
    // app and XREAL shows it as a flat MRSpace panel — which is our whole problem.
    string XrStatus()
    {
        bool deviceActive = XRSettings.isDeviceActive;
        string devName = string.IsNullOrEmpty(XRSettings.loadedDeviceName) ? "(none)" : XRSettings.loadedDeviceName;

        string loader = "(no XRGeneralSettings)";
        var gs = XRGeneralSettings.Instance;
        if (gs != null && gs.Manager != null)
            loader = gs.Manager.activeLoader != null ? gs.Manager.activeLoader.name : "(no activeLoader)";

        // Is there a running XR display subsystem (the thing that renders stereo)?
        var displays = new List<XRDisplaySubsystem>();
        SubsystemManager.GetInstances(displays);
        int running = 0;
        foreach (var d in displays) if (d.running) running++;

        return $"XR active:{deviceActive} dev:{devName}\nloader:{loader} displaysRunning:{running}/{displays.Count}";
    }

    bool _showDebug = false;   // calibration panel open?

    void OnGUI()
    {
        int w = Screen.width;
        int h = Screen.height;
        float big = Mathf.Max(w, h);
        bool eyeRunning = useEyeCamera && _eyeReady;

        var btn = new GUIStyle(GUI.skin.button)
        { fontSize = Mathf.RoundToInt(big * 0.026f), fontStyle = FontStyle.Bold };
        var lbl = new GUIStyle(GUI.skin.label)
        { fontSize = Mathf.RoundToInt(big * 0.030f), fontStyle = FontStyle.Bold, wordWrap = true };
        lbl.normal.textColor = Color.white;
        var mid = new GUIStyle(lbl) { alignment = TextAnchor.MiddleCenter };

        // ── Panel closed: clean AR, corner "SNAP" + "AI" + "CAL" buttons ─────
        if (!_showDebug)
        {
            float bw = big * 0.13f, bh = big * 0.055f;
            GUI.color = new Color(1, 1, 1, 0.45f);
            if (GUI.Button(new Rect(w - bw - 12, 12, bw, bh), "CAL", btn)) _showDebug = true;
            if (GUI.Button(new Rect(w - 2 * (bw + 12), 12, bw, bh),
                           enableAnalyze ? "AI:ON" : "AI:OFF", btn))
                enableAnalyze = !enableAnalyze;
            if (GUI.Button(new Rect(w - 3 * (bw + 12), 12, bw, bh),
                           _snapping ? "..." : "SNAP", btn) && !_snapping)
                StartCoroutine(TakeSnapshot());
            if (_recording) GUI.color = new Color(1f, 0.25f, 0.25f, 0.85f);
            if (GUI.Button(new Rect(w - 4 * (bw + 12), 12, bw, bh),
                           _recording ? "STOP●" : "REC", btn) && !_recBusy)
                StartCoroutine(ToggleRecord());
            GUI.color = Color.white;

            // Snapshot feedback (URL of the saved picture, or error)
            if (!string.IsNullOrEmpty(_snapMsg) && Time.time < _snapMsgUntil)
                GUI.Label(new Rect(20, 12 + bh + 8, w - 40, h * 0.12f), _snapMsg, lbl);

            if (!eyeRunning)   // still starting up → show a hint
                GUI.Label(new Rect(20, 16, w - 40, h * 0.18f),
                    $"Connecting Eye camera...\n{_status}", lbl);
            return;
        }

        // ── Panel open: dark background + live calibration controls ──────────
        GUI.color = new Color(0, 0, 0, 0.92f);
        GUI.Box(new Rect(0, 0, w, h), "");
        GUI.color = Color.white;

        GUI.Label(new Rect(16, 10, w - 32, h * 0.14f),
            $"DET c:{_dbgCenter.x:F2},{_dbgCenter.y:F2}   REND:{_dbgPxNorm.x:F2},{_dbgPxNorm.y:F2}\n" +
            $"canvas:{_dbgCanvas.x:F0}x{_dbgCanvas.y:F0}  trk:{_dbgTracks}  {_lastServerMsg}", lbl);

        float y = h * 0.17f;
        float rowH = h * 0.085f, pad = 10f;
        float c3 = (w - 32) / 3f;            // 3-column rows
        float c4 = (w - 32) / 4f;            // 4-column rows

        // eyeCameraFovH  (left/right tracking) — reduce until box stays on apple as you turn
        if (GUI.Button(new Rect(16, y, c3 - pad, rowH), "FOVH −2", btn)) eyeCameraFovH -= 2f;
        GUI.Label(new Rect(16 + c3, y, c3 - pad, rowH), $"H {eyeCameraFovH:F0}", mid);
        if (GUI.Button(new Rect(16 + 2 * c3, y, c3 - pad, rowH), "FOVH +2", btn)) eyeCameraFovH += 2f;
        y += rowH + pad;

        // eyeCameraFovV (up/down tracking)
        if (GUI.Button(new Rect(16, y, c3 - pad, rowH), "FOVV −2", btn)) eyeCameraFovV -= 2f;
        GUI.Label(new Rect(16 + c3, y, c3 - pad, rowH), $"V {eyeCameraFovV:F0}", mid);
        if (GUI.Button(new Rect(16 + 2 * c3, y, c3 - pad, rowH), "FOVV +2", btn)) eyeCameraFovV += 2f;
        y += rowH + pad;

        // box size
        if (GUI.Button(new Rect(16, y, c3 - pad, rowH), "SIZE −", btn)) boxSizeScale = Mathf.Max(0.1f, boxSizeScale - 0.05f);
        GUI.Label(new Rect(16 + c3, y, c3 - pad, rowH), $"sz {boxSizeScale:F2}", mid);
        if (GUI.Button(new Rect(16 + 2 * c3, y, c3 - pad, rowH), "SIZE +", btn)) boxSizeScale += 0.05f;
        y += rowH + pad;

        // aim offset (centre a centred apple)
        if (GUI.Button(new Rect(16, y, c4 - pad, rowH), "AIMX −", btn)) aimOffsetDeg.x -= 0.5f;
        if (GUI.Button(new Rect(16 + c4, y, c4 - pad, rowH), "AIMX +", btn)) aimOffsetDeg.x += 0.5f;
        if (GUI.Button(new Rect(16 + 2 * c4, y, c4 - pad, rowH), "AIMY −", btn)) aimOffsetDeg.y -= 0.5f;
        if (GUI.Button(new Rect(16 + 3 * c4, y, c4 - pad, rowH), "AIMY +", btn)) aimOffsetDeg.y += 0.5f;
        y += rowH + pad;
        GUI.Label(new Rect(16, y, w - 32, rowH * 0.6f), $"aim {aimOffsetDeg.x:F1},{aimOffsetDeg.y:F1}", lbl);
        y += rowH * 0.6f + pad;

        // motion smoothing (box glide speed; sizeSmoothing follows at 80%)
        if (GUI.Button(new Rect(16, y, c3 - pad, rowH), "SMOOTH −", btn))
        { posSmoothing = Mathf.Max(2f, posSmoothing - 2f); sizeSmoothing = posSmoothing * 0.8f; }
        GUI.Label(new Rect(16 + c3, y, c3 - pad, rowH), $"sm {posSmoothing:F0}", mid);
        if (GUI.Button(new Rect(16 + 2 * c3, y, c3 - pad, rowH), "SMOOTH +", btn))
        { posSmoothing = Mathf.Min(30f, posSmoothing + 2f); sizeSmoothing = posSmoothing * 0.8f; }
        y += rowH + pad;

        // flip horizontal (if box moves the wrong way) + close
        if (GUI.Button(new Rect(16, y, c3 - pad, rowH), "FLIP X", btn)) eyeCameraFovH = -eyeCameraFovH;
        if (GUI.Button(new Rect(16 + c3, y, c3 - pad, rowH), enableAnalyze ? "AI:ON" : "AI:OFF", btn)) enableAnalyze = !enableAnalyze;
        if (GUI.Button(new Rect(16 + 2 * c3, y, c3 - pad, rowH), "CLOSE", btn)) _showDebug = false;
    }
}
