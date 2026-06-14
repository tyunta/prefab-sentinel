#nullable disable
using System;
using System.Collections.Generic;
using UnityEditor;

// AssetDatabase-backed destructive asset delete handler.
namespace PrefabSentinel
{
    public static partial class UnityEditorControlBridge
    {
        private static EditorControlResponse HandleDeleteAssets(
            EditorControlRequest request)
        {
            if (!request.confirm || string.IsNullOrWhiteSpace(request.change_reason))
                return BuildError(
                    "DELETE_ASSETS_AUDIT_REQUIRED",
                    "delete_assets requires confirm=true and change_reason.");

            if (!TryParseDeleteAssetPaths(
                request.asset_paths_json, out List<string> assetPaths))
            {
                return BuildError(
                    "DELETE_ASSETS_BAD_PAYLOAD",
                    "asset_paths_json must be a non-empty JSON string array.");
            }

            foreach (string assetPath in assetPaths)
            {
                if (!IsSupportedDeleteAssetPath(assetPath))
                {
                    return BuildError(
                        "DELETE_ASSETS_UNSUPPORTED_PATH",
                        $"delete_assets only supports normalized Assets/ paths: {assetPath}",
                        new EditorControlData
                        {
                            asset_path = assetPath,
                            read_only = false,
                            executed = false
                        });
                }
            }

            var failedPaths = new List<string>();
            bool ok = AssetDatabase.DeleteAssets(
                assetPaths.ToArray(), failedPaths);
            AssetDatabase.Refresh();

            var deletedPaths = new List<string>(assetPaths);
            foreach (string failedPath in failedPaths)
            {
                deletedPaths.Remove(failedPath);
            }

            var data = new EditorControlData
            {
                read_only = false,
                executed = ok && failedPaths.Count == 0,
                total_entries = assetPaths.Count,
                deleted_paths = deletedPaths.ToArray(),
                failed_paths = failedPaths.ToArray()
            };

            if (!ok || failedPaths.Count > 0)
            {
                return BuildError(
                    "DELETE_ASSETS_FAILED",
                    "AssetDatabase.DeleteAssets reported failed paths.",
                    data);
            }

            return BuildSuccess(
                "DELETE_ASSETS_OK",
                "AssetDatabase.DeleteAssets completed.",
                data);
        }

        private static bool TryParseDeleteAssetPaths(
            string json, out List<string> assetPaths)
        {
            assetPaths = new List<string>();
            if (!JsonArrayScalarParser.TryParse(
                json, out List<JsonArrayScalar> elements))
            {
                return false;
            }
            if (elements.Count == 0) return false;

            foreach (JsonArrayScalar element in elements)
            {
                if (element.Kind != JsonArrayScalarKind.String) return false;
                if (string.IsNullOrWhiteSpace(element.Value)) return false;
                assetPaths.Add(element.Value);
            }
            return true;
        }

        private static bool IsSupportedDeleteAssetPath(string assetPath)
        {
            if (string.IsNullOrWhiteSpace(assetPath)) return false;
            if (assetPath.IndexOf('\\') >= 0) return false;
            if (!assetPath.StartsWith("Assets/", StringComparison.Ordinal))
                return false;

            string[] parts = assetPath.Split('/');
            foreach (string part in parts)
            {
                if (part.Length == 0 || part == "." || part == "..")
                    return false;
            }
            return true;
        }
    }
}
