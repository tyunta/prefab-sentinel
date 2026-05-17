using System;

// UI element type allow-listing and font-missing message selection —
// Unity-free decisions extracted from HandleEditorCreateUiElement
// (issues #195 / #205 / H-5).
namespace PrefabSentinel
{
    /// <summary>
    /// The fixed set of supported uGUI element type tokens accepted by the
    /// <c>editor_create_ui_element</c> surface.
    /// </summary>
    internal static class UiElementTypeAllowlist
    {
        internal static readonly string[] AllowedTypes =
            { "Image", "TextMeshProUGUI", "Button", "Slider", "Toggle" };

        /// <summary>
        /// Return true when <paramref name="typeToken"/> is one of the
        /// supported UI element type tokens.
        /// </summary>
        public static bool IsAllowed(string typeToken)
        {
            return Array.IndexOf(AllowedTypes, typeToken) >= 0;
        }
    }

    /// <summary>
    /// Selects the TextMeshPro font-missing warning message. When the caller
    /// supplied no font path the resolver fell back to the canonical default
    /// path; otherwise it attempted the caller-supplied path. The message
    /// names the path actually relied on.
    /// </summary>
    internal static class UiFontMissingMessage
    {
        public static string ForMissingFont(
            string callerFontPath, string canonicalDefaultPath, string elementPath)
        {
            if (string.IsNullOrEmpty(callerFontPath))
            {
                return "TextMeshPro font asset missing at canonical default path "
                    + $"'{canonicalDefaultPath}'. "
                    + $"GameObject created at {elementPath} with TextMeshProUGUI attached "
                    + "but no font assigned.";
            }
            return "TextMeshPro font asset missing at caller-supplied path "
                + $"'{callerFontPath}'. "
                + $"GameObject created at {elementPath} with TextMeshProUGUI attached "
                + "but no font assigned.";
        }
    }
}
