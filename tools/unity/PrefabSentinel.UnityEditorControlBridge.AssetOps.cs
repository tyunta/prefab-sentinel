using System;
using System.IO;

// Shared constants and helpers for the editor asset operation partials.
namespace PrefabSentinel
{
    public static partial class UnityEditorControlBridge
    {
        private const string GeneratedAssetType = "render_texture";
        private const string GeneratedAssetUnityType = "RenderTexture";
        private const string PartialSideEffectCode =
            "PARTIAL_SIDE_EFFECT_REQUIRES_REVIEW";

        private static EditorControlResponse WithPartialSideEffectDiagnostic(
            EditorControlResponse response)
        {
            response.diagnostics = new[]
            {
                new EditorControlDiagnostic
                {
                    code = PartialSideEffectCode,
                    severity = "warning",
                    detail = "Operation state must be reviewed in Unity.",
                    evidence = response.code,
                },
            };
            return response;
        }

        private static TEnum ParseEnum<TEnum>(string value)
            where TEnum : struct
        {
            if (Enum.TryParse(value, false, out TEnum parsed))
            {
                return parsed;
            }
            throw new ArgumentException(
                $"Unsupported {typeof(TEnum).Name} value: {value}");
        }

        private static bool MetaFileExists(string assetPath)
        {
            string relativePath = assetPath.Replace(
                "/",
                Path.DirectorySeparatorChar.ToString());
            return File.Exists(Path.Combine(
                CurrentProjectRoot(),
                relativePath + ".meta"));
        }

        private static string ParentPath(string assetPath)
        {
            int index = assetPath.LastIndexOf(
                "/",
                StringComparison.Ordinal);
            return index < 0 ? string.Empty : assetPath.Substring(0, index);
        }
    }
}
