using System;
using System.Collections.Generic;
using TMPro;
using UnityEditor;
using UnityEngine;
using UnityEngine.UI;

namespace PrefabSentinel
{
    /// <summary>
    /// Issue #195 — dedicated uGUI element creation handler.
    ///
    /// ``editor_create_primitive`` is a thin wrapper over
    /// ``GameObject.CreatePrimitive`` and only accepts the six built-in
    /// mesh ``PrimitiveType`` values. uGUI elements (Image, TMP,
    /// Button, Slider, Toggle) need a rect-bearing GameObject with a
    /// dedicated control attached and benefit from first-class
    /// rect/anchor parameters and an explicit TextMeshPro font default.
    /// Splitting the surfaces keeps each one's name truthful about its
    /// scope and lets the agent caller pick the right tool from the
    /// signature alone.
    /// </summary>
    public static partial class UnityEditorControlBridge
    {
        // The canonical default font asset for TextMeshPro elements. The
        // ``knowledge/prefab-sentinel-saveasprefabasset-pitfalls.md`` §3
        // entry pins this path as the reliable assignment that avoids the
        // ``TMP_Settings.defaultFontAsset`` fontSize-mismatch trap.
        private const string UiElementDefaultTmpFontAssetPath =
            "Assets/TextMesh Pro/Resources/Fonts & Materials/LiberationSans SDF.asset";

        // The canonical allowed type set. Source-text invariants pin
        // each token literally so a future edit cannot silently drop or
        // add a token without breaking the test.
        private static readonly string[] UiElementAllowedTypes =
        {
            "Image",
            "TextMeshProUGUI",
            "Button",
            "Slider",
            "Toggle",
        };

        [Serializable]
        private sealed class UiRectPayload
        {
            public float[] anchorMin = null;
            public float[] anchorMax = null;
            public float[] sizeDelta = null;
        }

        [Serializable]
        private sealed class UiPropertiesPayload
        {
            // ``color`` is delivered as a 4-element RGBA float array.
            public float[] color = null;
            // ``font`` is delivered as an asset path string.
            public string font = string.Empty;
        }

        private static EditorControlResponse HandleEditorCreateUiElement(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.new_name))
                return BuildError(
                    "EDITOR_CTRL_CREATE_UI_NO_NAME",
                    "new_name (UI element name) is required.");

            string requestedType = request.component_type ?? string.Empty;
            if (Array.IndexOf(UiElementAllowedTypes, requestedType) < 0)
                return BuildError(
                    "EDITOR_CTRL_CREATE_UI_BAD_TYPE",
                    $"Invalid UI element type: '{requestedType}'. " +
                    "Allowed: " + string.Join(", ", UiElementAllowedTypes) + ".",
                    data: new EditorControlData
                    {
                        suggestions = (string[])UiElementAllowedTypes.Clone(),
                    });

            Transform parentTransform = null;
            if (!string.IsNullOrEmpty(request.hierarchy_path))
            {
                var parentGo = GameObject.Find(request.hierarchy_path);
                if (parentGo == null)
                    return BuildError(
                        "EDITOR_CTRL_CREATE_UI_PARENT_NOT_FOUND",
                        $"Parent not found: {request.hierarchy_path}");
                parentTransform = parentGo.transform;
            }

            UiRectPayload rect = ParseUiRectPayload(request.ui_rect_json);
            UiPropertiesPayload props = ParseUiPropertiesPayload(request.ui_properties_json);

            // Build the rect-bearing GameObject. ``RectTransform`` is the
            // first component so the Inspector orders it the same way
            // Unity's GameObject > UI menu items do.
            var go = new GameObject(request.new_name, typeof(RectTransform));
            Undo.RegisterCreatedObjectUndo(go, $"PrefabSentinel: Create UI {requestedType}");
            if (parentTransform != null)
                Undo.SetTransformParent(go.transform, parentTransform,
                    $"PrefabSentinel: SetParent {go.name}");

            var rectTransform = (RectTransform)go.transform;
            ApplyRectPayload(rectTransform, rect);

            bool tmpFontMissing = false;
            switch (requestedType)
            {
                case "Image":
                    AttachImage(go, props);
                    break;
                case "TextMeshProUGUI":
                    tmpFontMissing = AttachTextMeshProUGUI(go, props);
                    break;
                case "Button":
                    AttachButton(go, props);
                    break;
                case "Slider":
                    AttachSlider(go, props);
                    break;
                case "Toggle":
                    AttachToggle(go, props);
                    break;
            }

            string elementPath = GetHierarchyPath(go.transform);
            if (tmpFontMissing)
            {
                var warnResp = BuildError(
                    "EDITOR_CTRL_CREATE_UI_TMP_FONT_MISSING",
                    $"TextMeshPro font asset missing at canonical default path " +
                    $"'{UiElementDefaultTmpFontAssetPath}'. " +
                    $"GameObject created at {elementPath} with TextMeshProUGUI attached " +
                    $"but no font assigned.",
                    data: new EditorControlData
                    {
                        selected_object = go.name,
                        output_path = elementPath,
                        executed = true,
                        read_only = false,
                    });
                warnResp.severity = "warning";
                return warnResp;
            }

            var resp = BuildSuccess(
                "EDITOR_CTRL_CREATE_UI_OK",
                $"Created UI element '{go.name}' ({requestedType}) at {elementPath}",
                data: new EditorControlData
                {
                    selected_object = go.name,
                    output_path = elementPath,
                    executed = true,
                    read_only = false,
                });
            resp.diagnostics = new[] { new EditorControlDiagnostic
            {
                detail = "Runtime modification — save the scene (File > Save) to persist.",
                evidence = "Undo.RegisterCreatedObjectUndo"
            }};
            return resp;
        }

        private static UiRectPayload ParseUiRectPayload(string json)
        {
            if (string.IsNullOrEmpty(json))
                return new UiRectPayload();
            try
            {
                return JsonUtility.FromJson<UiRectPayload>(json) ?? new UiRectPayload();
            }
            catch (Exception ex)
            {
                // Issue #195 / CLAUDE.md Unity rules: never swallow a
                // failure silently. The Python side always emits valid
                // JSON via ``dump_json``, so reaching this branch means
                // an unexpected Bridge input — log enough to diagnose.
                Debug.LogWarning(
                    "[PrefabSentinel] ParseUiRectPayload: malformed JSON, "
                    + "applying defaults: " + ex.Message);
                return new UiRectPayload();
            }
        }

        private static UiPropertiesPayload ParseUiPropertiesPayload(string json)
        {
            if (string.IsNullOrEmpty(json))
                return new UiPropertiesPayload();
            try
            {
                return JsonUtility.FromJson<UiPropertiesPayload>(json) ?? new UiPropertiesPayload();
            }
            catch (Exception ex)
            {
                Debug.LogWarning(
                    "[PrefabSentinel] ParseUiPropertiesPayload: malformed JSON, "
                    + "applying defaults: " + ex.Message);
                return new UiPropertiesPayload();
            }
        }

        private static void ApplyRectPayload(RectTransform rt, UiRectPayload rect)
        {
            if (rect.anchorMin != null && rect.anchorMin.Length >= 2)
                rt.anchorMin = new Vector2(rect.anchorMin[0], rect.anchorMin[1]);
            if (rect.anchorMax != null && rect.anchorMax.Length >= 2)
                rt.anchorMax = new Vector2(rect.anchorMax[0], rect.anchorMax[1]);
            if (rect.sizeDelta != null && rect.sizeDelta.Length >= 2)
                rt.sizeDelta = new Vector2(rect.sizeDelta[0], rect.sizeDelta[1]);
        }

        private static Color ColorFromPayload(float[] rgba, Color fallback)
        {
            if (rgba == null || rgba.Length < 4)
                return fallback;
            return new Color(rgba[0], rgba[1], rgba[2], rgba[3]);
        }

        private static void AttachImage(GameObject go, UiPropertiesPayload props)
        {
            var image = go.AddComponent<Image>();
            if (props.color != null && props.color.Length >= 4)
                image.color = ColorFromPayload(props.color, image.color);
        }

        /// <summary>
        /// Attaches a ``TextMeshProUGUI`` to ``go`` and assigns the
        /// caller-supplied font (``properties.font``) when present, the
        /// canonical default font asset otherwise. Returns ``true`` when
        /// neither path resolves so the caller can emit the documented
        /// font-missing warning envelope.
        /// </summary>
        private static bool AttachTextMeshProUGUI(GameObject go, UiPropertiesPayload props)
        {
            var tmp = go.AddComponent<TextMeshProUGUI>();
            if (props.color != null && props.color.Length >= 4)
                tmp.color = ColorFromPayload(props.color, tmp.color);

            string fontPath = string.IsNullOrEmpty(props.font)
                ? UiElementDefaultTmpFontAssetPath
                : props.font;
            var fontAsset = AssetDatabase.LoadAssetAtPath<TMP_FontAsset>(fontPath);
            if (fontAsset == null)
                return true;
            tmp.font = fontAsset;
            return false;
        }

        private static void AttachButton(GameObject go, UiPropertiesPayload props)
        {
            var image = go.AddComponent<Image>();
            if (props.color != null && props.color.Length >= 4)
                image.color = ColorFromPayload(props.color, image.color);
            go.AddComponent<Button>();
        }

        private static void AttachSlider(GameObject go, UiPropertiesPayload props)
        {
            // ``Slider`` requires a Graphic on the same GameObject for the
            // event-system raycast to resolve; attach an Image with the
            // documented graphic-color fallback.
            var image = go.AddComponent<Image>();
            if (props.color != null && props.color.Length >= 4)
                image.color = ColorFromPayload(props.color, image.color);
            go.AddComponent<Slider>();
        }

        private static void AttachToggle(GameObject go, UiPropertiesPayload props)
        {
            var image = go.AddComponent<Image>();
            if (props.color != null && props.color.Length >= 4)
                image.color = ColorFromPayload(props.color, image.color);
            go.AddComponent<Toggle>();
        }
    }
}
