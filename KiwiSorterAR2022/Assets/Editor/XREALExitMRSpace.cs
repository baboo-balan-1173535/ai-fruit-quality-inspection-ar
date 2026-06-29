// XREALExitMRSpace.cs — post-build hook to take the app OUT of MRSpace.
//
// The XREAL SDK's precompiled AAR injects two things that force the app to run
// as a flat panel inside the MRSpace shell (the dotted grid + APP/home chrome):
//   1. ai.nreal.activitylife.NRXRActivity  — a second LAUNCHER activity that
//      hosts the app inside MRSpace. ControlGlasses prefers it over Unity's own
//      activity, so the app always opens in MRSpace.
//   2. <meta-data nr_features="multiResume"> — engages the multi-resume / MRSpace
//      windowing behaviour in the SDK runtime.
//
// Neither can be removed from the XREALSettings UI, and editing the
// Assets/Plugins/Android manifest does NOT work because that becomes the
// low-priority unityLibrary manifest, processed BEFORE the AAR is merged.
//
// The only manifest with high enough priority to override AAR-injected elements
// is launcher/src/main/AndroidManifest.xml. Unity generates it automatically, so
// we patch it here in IPostGenerateGradleAndroidProject — after Unity writes it,
// before Gradle merges the AAR. We add Android manifest-merger directives:
//   - strip NRXRActivity's intent-filter  -> it is no longer a launcher
//   - remove the nr_features meta-data     -> no MRSpace windowing
// UnityPlayerActivity keeps its own LAUNCHER (added by the SDK when
// SupportMultiResume is OFF), so the app stays launchable and opens directly.

#if UNITY_ANDROID
using System.IO;
using System.Xml;
using UnityEditor.Android;
using UnityEngine;

public class XREALExitMRSpace : IPostGenerateGradleAndroidProject
{
    // Run late so we patch after other providers have written the manifest.
    public int callbackOrder => 999;

    const string ToolsNs = "http://schemas.android.com/tools";
    const string AndroidNs = "http://schemas.android.com/apk/res/android";

    public void OnPostGenerateGradleAndroidProject(string unityLibraryPath)
    {
        // `unityLibraryPath` points at the unityLibrary module. The launcher
        // module — the one whose manifest wins the merge — is its sibling.
        string gradleRoot = Path.GetDirectoryName(unityLibraryPath);
        string manifestPath = Path.Combine(gradleRoot, "launcher", "src", "main", "AndroidManifest.xml");

        if (!File.Exists(manifestPath))
        {
            Debug.LogWarning($"[XREALExitMRSpace] launcher manifest not found at {manifestPath} — MRSpace patch skipped.");
            return;
        }

        var doc = new XmlDocument();
        doc.Load(manifestPath);

        var nsmgr = new XmlNamespaceManager(doc.NameTable);
        nsmgr.AddNamespace("android", AndroidNs);
        nsmgr.AddNamespace("tools", ToolsNs);

        var manifest = doc.DocumentElement;

        // Ensure the tools namespace is declared on <manifest>
        if (manifest.GetAttribute("xmlns:tools") == "")
            manifest.SetAttribute("xmlns:tools", ToolsNs);

        var application = manifest.SelectSingleNode("application", nsmgr) as XmlElement;
        if (application == null)
        {
            Debug.LogWarning("[XREALExitMRSpace] <application> not found — MRSpace patch skipped.");
            return;
        }

        // 1) Strip NRXRActivity's launcher intent-filter.
        //    <activity android:name="ai.nreal.activitylife.NRXRActivity"
        //              android:exported="true" tools:node="merge">
        //        <intent-filter tools:node="removeAll"/>
        //    </activity>
        var activity = doc.CreateElement("activity");
        activity.SetAttribute("name", AndroidNs, "ai.nreal.activitylife.NRXRActivity");
        // exported is explicit so the merger never trips the Android-12 rule
        activity.SetAttribute("exported", AndroidNs, "true");
        activity.SetAttribute("node", ToolsNs, "merge");

        var intentFilter = doc.CreateElement("intent-filter");
        intentFilter.SetAttribute("node", ToolsNs, "removeAll");
        activity.AppendChild(intentFilter);
        application.AppendChild(activity);

        // 2) Remove the multiResume feature flag.
        //    <meta-data android:name="nr_features" tools:node="remove"/>
        var meta = doc.CreateElement("meta-data");
        meta.SetAttribute("name", AndroidNs, "nr_features");
        meta.SetAttribute("node", ToolsNs, "remove");
        application.AppendChild(meta);

        doc.Save(manifestPath);
        Debug.Log($"[XREALExitMRSpace] Patched launcher manifest to exit MRSpace: {manifestPath}");
    }
}
#endif
