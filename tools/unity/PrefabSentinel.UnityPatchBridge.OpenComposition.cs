using System;
using System.Collections.Generic;
using UnityEditor;
using UnityEngine;

namespace PrefabSentinel
{
    public static partial class UnityPatchBridge
    {
        private static bool TryApplyOpenPrefabOp(
            GameObject prefabRoot,
            string requestTarget,
            PatchOp op,
            int opIndex,
            Dictionary<string, UnityEngine.Object> handles,
            List<BridgeDiagnostic> diagnostics
        )
        {
            if (op == null || op.op == null)
            {
                return TryApplyOp(prefabRoot, requestTarget, op, opIndex, diagnostics);
            }

            string opName = op.op.Trim();
            if (string.Equals(opName, "instantiate_prefab", StringComparison.Ordinal))
            {
                return TryInstantiateOpenPrefab(
                    requestTarget, op, opIndex, handles, diagnostics);
            }
            if (string.Equals(opName, "rename_object", StringComparison.Ordinal))
            {
                return TryRenameOpenPrefabObject(
                    requestTarget, op, opIndex, handles, diagnostics);
            }
            if (string.Equals(opName, "find_game_object", StringComparison.Ordinal))
            {
                return TryFindOpenPrefabGameObject(
                    prefabRoot, requestTarget, op, opIndex, handles, diagnostics);
            }
            if (string.Equals(opName, "find_component", StringComparison.Ordinal))
            {
                return TryFindOpenPrefabComponent(
                    requestTarget, op, opIndex, handles, diagnostics);
            }
            if (string.Equals(opName, "set", StringComparison.Ordinal)
                && !string.IsNullOrWhiteSpace(op.target))
            {
                return TryApplyOpenPrefabSet(
                    requestTarget, op, opIndex, handles, diagnostics);
            }
            return TryApplyOp(prefabRoot, requestTarget, op, opIndex, diagnostics);
        }

        private static bool TryInstantiateOpenPrefab(
            string requestTarget,
            PatchOp op,
            int opIndex,
            Dictionary<string, UnityEngine.Object> handles,
            List<BridgeDiagnostic> diagnostics
        )
        {
            if (!TryResolveAssetPath(
                    op.prefab, false, out string sourcePath, out string pathError))
            {
                return FailOpenPrefabOp(
                    requestTarget, opIndex, "prefab", "apply_error", pathError, diagnostics);
            }
            GameObject sourcePrefab = AssetDatabase.LoadAssetAtPath<GameObject>(sourcePath);
            if (sourcePrefab == null)
            {
                return FailOpenPrefabOp(
                    requestTarget,
                    opIndex,
                    "prefab",
                    "apply_error",
                    $"prefab asset was not found: '{sourcePath}'",
                    diagnostics
                );
            }
            if (!TryResolveGameObjectHandle(
                    op.parent, handles, out GameObject parent, out string parentError))
            {
                return FailOpenPrefabOp(
                    requestTarget, opIndex, "parent", "schema_error", parentError, diagnostics);
            }

            GameObject instance =
                PrefabUtility.InstantiatePrefab(sourcePrefab, parent.transform) as GameObject;
            if (instance == null)
            {
                return FailOpenPrefabOp(
                    requestTarget,
                    opIndex,
                    string.Empty,
                    "apply_error",
                    "PrefabUtility.InstantiatePrefab returned null",
                    diagnostics
                );
            }
            if (!TryRegisterHandle(
                    op.result, instance, handles, requestTarget, opIndex, diagnostics))
            {
                UnityEngine.Object.DestroyImmediate(instance);
                return false;
            }
            return true;
        }

        private static bool TryRenameOpenPrefabObject(
            string requestTarget,
            PatchOp op,
            int opIndex,
            Dictionary<string, UnityEngine.Object> handles,
            List<BridgeDiagnostic> diagnostics
        )
        {
            if (!TryResolveGameObjectHandle(
                    op.target, handles, out GameObject target, out string targetError))
            {
                return FailOpenPrefabOp(
                    requestTarget, opIndex, "target", "schema_error", targetError, diagnostics);
            }
            if (string.IsNullOrWhiteSpace(op.name))
            {
                return FailOpenPrefabOp(
                    requestTarget,
                    opIndex,
                    "name",
                    "schema_error",
                    "rename_object requires name",
                    diagnostics
                );
            }
            target.name = op.name.Trim();
            return true;
        }

        private static bool TryFindOpenPrefabGameObject(
            GameObject prefabRoot,
            string requestTarget,
            PatchOp op,
            int opIndex,
            Dictionary<string, UnityEngine.Object> handles,
            List<BridgeDiagnostic> diagnostics
        )
        {
            GameObject found;
            string error;
            string field;
            if (!string.IsNullOrWhiteSpace(op.target)
                || !string.IsNullOrWhiteSpace(op.relative_symbol_path))
            {
                field = "relative_symbol_path";
                if (!TryResolveRelativeGameObject(op, handles, out found, out error))
                {
                    return FailOpenPrefabOp(
                        requestTarget, opIndex, field, "apply_error", error, diagnostics);
                }
            }
            else
            {
                field = string.IsNullOrWhiteSpace(op.file_id) ? "symbol_path" : "file_id";
                if (!TryResolveExistingGameObject(prefabRoot, op, out found, out error))
                {
                    return FailOpenPrefabOp(
                        requestTarget, opIndex, field, "apply_error", error, diagnostics);
                }
            }
            return TryRegisterHandle(
                op.result, found, handles, requestTarget, opIndex, diagnostics);
        }

        private static bool TryFindOpenPrefabComponent(
            string requestTarget,
            PatchOp op,
            int opIndex,
            Dictionary<string, UnityEngine.Object> handles,
            List<BridgeDiagnostic> diagnostics
        )
        {
            if (!TryResolveGameObjectHandle(
                    op.target, handles, out GameObject target, out string targetError))
            {
                return FailOpenPrefabOp(
                    requestTarget, opIndex, "target", "schema_error", targetError, diagnostics);
            }
            if (!TryFindUniqueComponentOnObject(
                    target, op.type, out Component component, out string componentError))
            {
                return FailOpenPrefabOp(
                    requestTarget,
                    opIndex,
                    "type",
                    "apply_error",
                    componentError,
                    diagnostics
                );
            }
            return TryRegisterHandle(
                op.result, component, handles, requestTarget, opIndex, diagnostics);
        }

        private static bool TryApplyOpenPrefabSet(
            string requestTarget,
            PatchOp op,
            int opIndex,
            Dictionary<string, UnityEngine.Object> handles,
            List<BridgeDiagnostic> diagnostics
        )
        {
            if (!TryResolveComponentHandle(
                    op.target, handles, out Component component, out string componentError))
            {
                return FailOpenPrefabOp(
                    requestTarget,
                    opIndex,
                    "target",
                    "schema_error",
                    componentError,
                    diagnostics
                );
            }
            s_currentHandles = handles;
            try
            {
                if (!TryApplyMutationOpToObject(
                        component, requestTarget, op, opIndex, diagnostics))
                    return false;
                if (!UnityEditorControlBridge.TrySynchronizeUdonSharpProxy(
                        component, out string syncError))
                {
                    return FailOpenPrefabOp(
                        requestTarget,
                        opIndex,
                        "path",
                        "udonsharp_sync_error",
                        syncError,
                        diagnostics
                    );
                }
                return true;
            }
            finally
            {
                s_currentHandles = null;
            }
        }

        private static bool TryResolveExistingGameObject(
            GameObject prefabRoot,
            PatchOp op,
            out GameObject result,
            out string error
        )
        {
            result = null;
            error = string.Empty;
            bool hasPath = !string.IsNullOrWhiteSpace(op.symbol_path);
            bool hasFileId = !string.IsNullOrWhiteSpace(op.file_id);
            if (hasPath == hasFileId)
            {
                error = "find_game_object requires exactly one symbol_path or file_id";
                return false;
            }
            if (hasFileId)
            {
                return TryResolveGameObjectFileId(
                    prefabRoot, op.file_id, out result, out error);
            }
            return TryResolveStrictGameObjectPath(
                prefabRoot, op.symbol_path, out result, out error);
        }

        private static bool TryResolveRelativeGameObject(
            PatchOp op,
            Dictionary<string, UnityEngine.Object> handles,
            out GameObject result,
            out string error
        )
        {
            result = null;
            if (!TryResolveGameObjectHandle(
                    op.target, handles, out GameObject relativeRoot, out error))
            {
                return false;
            }
            return TryResolveStrictGameObjectPath(
                relativeRoot, op.relative_symbol_path, out result, out error);
        }

        private static bool TryResolveStrictGameObjectPath(
            GameObject root,
            string rawPath,
            out GameObject result,
            out string error
        )
        {
            result = null;
            error = string.Empty;
            if (!IsStrictRelativeSymbolPath(rawPath, out error))
            {
                return false;
            }
            string selector = root.name + "/" + rawPath;
            if (!TryResolveHierarchyPathWithResolver(
                    root, selector, out Transform target, out error))
            {
                return false;
            }
            result = target.gameObject;
            return true;
        }

        private static bool IsStrictRelativeSymbolPath(string rawPath, out string error)
        {
            error = string.Empty;
            if (string.IsNullOrWhiteSpace(rawPath) || rawPath != rawPath.Trim())
            {
                error = "symbol path must be a non-empty strict relative path";
                return false;
            }
            string[] segments = rawPath.Split('/');
            for (int i = 0; i < segments.Length; i++)
            {
                string segment = segments[i];
                if (string.IsNullOrEmpty(segment)
                    || segment == "."
                    || segment == ".."
                    || segment.IndexOf('\\') >= 0
                    || segment.IndexOf('*') >= 0
                    || !HasValidSiblingSelector(segment))
                {
                    error = $"invalid strict symbol path segment '{segment}'";
                    return false;
                }
            }
            return true;
        }

        private static bool HasValidSiblingSelector(string segment)
        {
            int hash = segment.LastIndexOf('#');
            if (hash < 0)
            {
                return true;
            }
            if (hash == 0 || hash == segment.Length - 1)
            {
                return false;
            }
            for (int i = hash + 1; i < segment.Length; i++)
            {
                if (!char.IsDigit(segment[i]))
                {
                    return false;
                }
            }
            return true;
        }

        private static bool TryResolveGameObjectFileId(
            GameObject root,
            string rawFileId,
            out GameObject result,
            out string error
        )
        {
            result = null;
            error = string.Empty;
            string fileId = rawFileId == null ? string.Empty : rawFileId.Trim();
            if (!ulong.TryParse(fileId, out ulong targetFileId))
            {
                error = $"file_id '{rawFileId}' is not a valid fileID";
                return false;
            }
            Transform[] transforms = root.GetComponentsInChildren<Transform>(true);
            for (int i = 0; i < transforms.Length; i++)
            {
                GameObject candidate = transforms[i].gameObject;
                GlobalObjectId gid = GlobalObjectId.GetGlobalObjectIdSlow(candidate);
                if (gid.targetObjectId == targetFileId)
                {
                    result = candidate;
                    return true;
                }
            }
            error = $"game object with file_id '{fileId}' was not found in the asset";
            return false;
        }

        private static bool FailOpenPrefabOp(
            string requestTarget,
            int opIndex,
            string field,
            string detail,
            string evidence,
            List<BridgeDiagnostic> diagnostics
        )
        {
            string location = string.IsNullOrEmpty(field)
                ? $"ops[{opIndex}]"
                : $"ops[{opIndex}].{field}";
            diagnostics.Add(
                new BridgeDiagnostic
                {
                    path = requestTarget,
                    location = location,
                    detail = detail,
                    evidence = evidence
                }
            );
            return false;
        }
    }
}
