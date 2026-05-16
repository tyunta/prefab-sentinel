using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;
namespace PrefabSentinel
{
    public static partial class UnityPatchBridge
    {
        private static BridgeResponse ApplyAssetOperations(BridgeRequest request, string assetPath)
        {
            int applied = 0;
            List<BridgeDiagnostic> diagnostics = new List<BridgeDiagnostic>();
            UnityEngine.Object assetObject = null;
            try
            {
                assetObject = AssetDatabase.LoadMainAssetAtPath(assetPath);
                if (assetObject == null)
                {
                    return BuildError(
                        "UNITY_BRIDGE_ASSET_LOAD",
                        "Failed to load asset contents.",
                        request.target,
                        request.ops.Length,
                        executed: true
                    );
                }

                string assetExtension = Path.GetExtension(assetPath);
                if (
                    string.Equals(assetExtension, ".mat", StringComparison.OrdinalIgnoreCase)
                    && !(assetObject is Material)
                )
                {
                    return BuildError(
                        "UNITY_BRIDGE_TARGET_UNSUPPORTED",
                        "Material operations require a .mat asset whose main object is UnityEngine.Material.",
                        request.target,
                        request.ops.Length,
                        executed: false
                    );
                }
                if (
                    string.Equals(assetExtension, ".asset", StringComparison.OrdinalIgnoreCase)
                    && !(assetObject is ScriptableObject)
                )
                {
                    return BuildError(
                        "UNITY_BRIDGE_TARGET_UNSUPPORTED",
                        "Asset operations currently support ScriptableObject main assets only.",
                        request.target,
                        request.ops.Length,
                        executed: false
                    );
                }

                Dictionary<string, UnityEngine.Object> handles = new Dictionary<string, UnityEngine.Object>(StringComparer.Ordinal)
                {
                    [AssetHandleName] = assetObject
                };

                for (int i = 0; i < request.ops.Length; i++)
                {
                    PatchOp op = request.ops[i];
                    string opName = (op?.op ?? string.Empty).Trim();
                    if (
                        !string.Equals(opName, "set", StringComparison.Ordinal)
                        && !string.Equals(opName, "insert_array_element", StringComparison.Ordinal)
                        && !string.Equals(opName, "remove_array_element", StringComparison.Ordinal)
                    )
                    {
                        diagnostics.Add(
                            new BridgeDiagnostic
                            {
                                path = request.target,
                                location = $"ops[{i}].op",
                                detail = "schema_error",
                                evidence = $"unsupported asset open op '{opName}'"
                            }
                        );
                        return BuildError(
                            "UNITY_BRIDGE_SCHEMA",
                            "Invalid asset patch plan.",
                            request.target,
                            request.ops.Length,
                            executed: false,
                            applied: applied,
                            diagnostics: diagnostics.ToArray()
                        );
                    }

                    UnityEngine.Object targetAsset;
                    string handleError;
                    if (!TryResolveAssetHandle(op.target, handles, out targetAsset, out handleError))
                    {
                        diagnostics.Add(
                            new BridgeDiagnostic
                            {
                                path = request.target,
                                location = $"ops[{i}].target",
                                detail = "schema_error",
                                evidence = handleError
                            }
                        );
                        return BuildError(
                            "UNITY_BRIDGE_SCHEMA",
                            "Invalid asset patch plan.",
                            request.target,
                            request.ops.Length,
                            executed: false,
                            applied: applied,
                            diagnostics: diagnostics.ToArray()
                        );
                    }

                    if (!TryApplyMutationOpToObject(targetAsset, request.target, op, i, diagnostics))
                    {
                        return BuildError(
                            "UNITY_BRIDGE_APPLY",
                            "Failed to apply asset mutation.",
                            request.target,
                            request.ops.Length,
                            executed: true,
                            applied: applied,
                            diagnostics: diagnostics.ToArray()
                        );
                    }
                    applied += 1;
                }

                EditorUtility.SetDirty(assetObject);
                AssetDatabase.SaveAssets();
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                UnityEngine.Object reopened = AssetDatabase.LoadMainAssetAtPath(assetPath);
                if (reopened == null)
                {
                    diagnostics.Add(
                        new BridgeDiagnostic
                        {
                            path = request.target,
                            location = "save",
                            detail = "apply_error",
                            evidence = "AssetDatabase.LoadMainAssetAtPath returned null after save"
                        }
                    );
                    return BuildError(
                        "UNITY_BRIDGE_APPLY",
                        "Failed to reopen asset after save.",
                        request.target,
                        request.ops.Length,
                        executed: true,
                        applied: applied,
                        diagnostics: diagnostics.ToArray()
                    );
                }

                return new BridgeResponse
                {
                    protocol_version = ProtocolVersion,
                    success = true,
                    severity = "info",
                    code = "SER_APPLY_OK",
                    message = "SerializedObject patch applied to asset via Unity executeMethod.",
                    data = new BridgeData
                    {
                        target = request.target,
                        op_count = request.ops.Length,
                        applied = applied,
                        read_only = false,
                        executed = true,
                        protocol_version = ProtocolVersion
                    },
                    diagnostics = Array.Empty<BridgeDiagnostic>()
                };
            }
            catch (Exception ex)
            {
                diagnostics.Add(
                    new BridgeDiagnostic
                    {
                        path = request.target,
                        location = "apply",
                        detail = "exception",
                        evidence = ex.ToString()
                    }
                );
                return BuildError(
                    "UNITY_BRIDGE_APPLY_EXCEPTION",
                    $"Unexpected apply exception: {ex.Message}",
                    request.target,
                    request.ops.Length,
                    executed: true,
                    applied: applied,
                    diagnostics: diagnostics.ToArray()
                );
            }
        }
        private static BridgeResponse ApplyAssetCreateOperations(BridgeRequest request, string assetPath)
        {
            int applied = 0;
            List<BridgeDiagnostic> diagnostics = new List<BridgeDiagnostic>();
            UnityEngine.Object assetObject = null;
            bool saved = false;
            Dictionary<string, UnityEngine.Object> handles = new Dictionary<string, UnityEngine.Object>(StringComparer.Ordinal);
            try
            {
                string fullAssetPath = Path.Combine(
                    Path.GetFullPath(Path.Combine(Application.dataPath, "..")),
                    assetPath
                );
                if (File.Exists(fullAssetPath))
                {
                    return BuildError(
                        "UNITY_BRIDGE_TARGET_EXISTS",
                        "target file already exists.",
                        request.target,
                        request.ops.Length,
                        executed: false
                    );
                }

                string parentDir = Path.GetDirectoryName(fullAssetPath);
                if (string.IsNullOrWhiteSpace(parentDir) || !Directory.Exists(parentDir))
                {
                    return BuildError(
                        "UNITY_BRIDGE_TARGET_PATH",
                        "target directory was not found.",
                        request.target,
                        request.ops.Length,
                        executed: false
                    );
                }

                string assetExtension = Path.GetExtension(assetPath);
                for (int i = 0; i < request.ops.Length; i++)
                {
                    PatchOp op = request.ops[i];
                    string opName = (op?.op ?? string.Empty).Trim();
                    if (string.Equals(opName, "create_asset", StringComparison.Ordinal))
                    {
                        if (assetObject != null)
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].op",
                                    detail = "schema_error",
                                    evidence = "asset root may be created only once"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid asset create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }

                        string assetName = string.IsNullOrWhiteSpace(op.name)
                            ? Path.GetFileNameWithoutExtension(assetPath)
                            : op.name.Trim();
                        if (
                            string.Equals(assetExtension, ".mat", StringComparison.OrdinalIgnoreCase)
                        )
                        {
                            if (!string.IsNullOrWhiteSpace(op.type))
                            {
                                Type materialType;
                                string typeError;
                                if (!TryResolveType(op.type, out materialType, out typeError))
                                {
                                    diagnostics.Add(
                                        new BridgeDiagnostic
                                        {
                                            path = request.target,
                                            location = $"ops[{i}].type",
                                            detail = "apply_error",
                                            evidence = typeError
                                        }
                                    );
                                    return BuildError(
                                        "UNITY_BRIDGE_APPLY",
                                        "Failed to create material asset.",
                                        request.target,
                                        request.ops.Length,
                                        executed: true,
                                        applied: applied,
                                        diagnostics: diagnostics.ToArray()
                                    );
                                }
                                if (!typeof(Material).IsAssignableFrom(materialType))
                                {
                                    diagnostics.Add(
                                        new BridgeDiagnostic
                                        {
                                            path = request.target,
                                            location = $"ops[{i}].type",
                                            detail = "apply_error",
                                            evidence = $"type '{materialType.FullName ?? materialType.Name}' is not assignable to UnityEngine.Material"
                                        }
                                    );
                                    return BuildError(
                                        "UNITY_BRIDGE_APPLY",
                                        "Failed to create material asset.",
                                        request.target,
                                        request.ops.Length,
                                        executed: true,
                                        applied: applied,
                                        diagnostics: diagnostics.ToArray()
                                    );
                                }
                            }

                            string shaderName = (op.shader ?? string.Empty).Trim();
                            if (string.IsNullOrWhiteSpace(shaderName))
                            {
                                diagnostics.Add(
                                    new BridgeDiagnostic
                                    {
                                        path = request.target,
                                        location = $"ops[{i}].shader",
                                        detail = "schema_error",
                                        evidence = "create_asset requires shader for material resources"
                                    }
                                );
                                return BuildError(
                                    "UNITY_BRIDGE_SCHEMA",
                                    "Invalid asset create plan.",
                                    request.target,
                                    request.ops.Length,
                                    executed: false,
                                    applied: applied,
                                    diagnostics: diagnostics.ToArray()
                                );
                            }

                            Shader shader = Shader.Find(shaderName);
                            if (shader == null)
                            {
                                diagnostics.Add(
                                    new BridgeDiagnostic
                                    {
                                        path = request.target,
                                        location = $"ops[{i}].shader",
                                        detail = "apply_error",
                                        evidence = $"shader '{shaderName}' was not found"
                                    }
                                );
                                return BuildError(
                                    "UNITY_BRIDGE_APPLY",
                                    "Failed to create material asset.",
                                    request.target,
                                    request.ops.Length,
                                    executed: true,
                                    applied: applied,
                                    diagnostics: diagnostics.ToArray()
                                );
                            }

                            Material material = new Material(shader) { name = assetName };
                            AssetDatabase.CreateAsset(material, assetPath);
                            assetObject = material;
                        }
                        else if (
                            string.Equals(assetExtension, ".asset", StringComparison.OrdinalIgnoreCase)
                        )
                        {
                            Type assetType;
                            string typeError;
                            if (!TryResolveType(op.type, out assetType, out typeError))
                            {
                                diagnostics.Add(
                                    new BridgeDiagnostic
                                    {
                                        path = request.target,
                                        location = $"ops[{i}].type",
                                        detail = "apply_error",
                                        evidence = typeError
                                    }
                                );
                                return BuildError(
                                    "UNITY_BRIDGE_APPLY",
                                    "Failed to create ScriptableObject asset.",
                                    request.target,
                                    request.ops.Length,
                                    executed: true,
                                    applied: applied,
                                    diagnostics: diagnostics.ToArray()
                                );
                            }
                            if (
                                !typeof(ScriptableObject).IsAssignableFrom(assetType)
                                || assetType.IsAbstract
                                || assetType.ContainsGenericParameters
                            )
                            {
                                diagnostics.Add(
                                    new BridgeDiagnostic
                                    {
                                        path = request.target,
                                        location = $"ops[{i}].type",
                                        detail = "apply_error",
                                        evidence = $"type '{assetType.FullName ?? assetType.Name}' is not a concrete ScriptableObject"
                                    }
                                );
                                return BuildError(
                                    "UNITY_BRIDGE_APPLY",
                                    "Failed to create ScriptableObject asset.",
                                    request.target,
                                    request.ops.Length,
                                    executed: true,
                                    applied: applied,
                                    diagnostics: diagnostics.ToArray()
                                );
                            }

                            ScriptableObject scriptableObject = ScriptableObject.CreateInstance(assetType);
                            if (scriptableObject == null)
                            {
                                diagnostics.Add(
                                    new BridgeDiagnostic
                                    {
                                        path = request.target,
                                        location = $"ops[{i}].type",
                                        detail = "apply_error",
                                        evidence = $"ScriptableObject.CreateInstance returned null for '{assetType.FullName ?? assetType.Name}'"
                                    }
                                );
                                return BuildError(
                                    "UNITY_BRIDGE_APPLY",
                                    "Failed to create ScriptableObject asset.",
                                    request.target,
                                    request.ops.Length,
                                    executed: true,
                                    applied: applied,
                                    diagnostics: diagnostics.ToArray()
                                );
                            }
                            scriptableObject.name = assetName;
                            AssetDatabase.CreateAsset(scriptableObject, assetPath);
                            assetObject = scriptableObject;
                        }
                        else
                        {
                            return BuildError(
                                "UNITY_BRIDGE_TARGET_UNSUPPORTED",
                                "Asset create mode currently supports .mat and .asset targets only.",
                                request.target,
                                request.ops.Length,
                                executed: false
                            );
                        }

                        handles[AssetHandleName] = assetObject;
                        if (!TryRegisterHandle(op.result, assetObject, handles, request.target, i, diagnostics))
                        {
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid asset create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        applied += 1;
                        continue;
                    }

                    if (
                        string.Equals(opName, "set", StringComparison.Ordinal)
                        || string.Equals(opName, "insert_array_element", StringComparison.Ordinal)
                        || string.Equals(opName, "remove_array_element", StringComparison.Ordinal)
                    )
                    {
                        if (assetObject == null)
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].op",
                                    detail = "schema_error",
                                    evidence = $"{opName} requires a create_asset operation first"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid asset create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }

                        UnityEngine.Object targetAsset;
                        string handleError;
                        if (!TryResolveAssetHandle(op.target, handles, out targetAsset, out handleError))
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].target",
                                    detail = "schema_error",
                                    evidence = handleError
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid asset create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }

                        if (!TryApplyMutationOpToObject(targetAsset, request.target, op, i, diagnostics))
                        {
                            return BuildError(
                                "UNITY_BRIDGE_APPLY",
                                "Failed to apply asset mutation.",
                                request.target,
                                request.ops.Length,
                                executed: true,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        applied += 1;
                        continue;
                    }

                    if (string.Equals(opName, "save", StringComparison.Ordinal))
                    {
                        if (assetObject == null)
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].op",
                                    detail = "schema_error",
                                    evidence = "save requires a create_asset operation first"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid asset create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        if (i != request.ops.Length - 1)
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].op",
                                    detail = "schema_error",
                                    evidence = "save must be the final operation in create mode"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_SCHEMA",
                                "Invalid asset create plan.",
                                request.target,
                                request.ops.Length,
                                executed: false,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }

                        EditorUtility.SetDirty(assetObject);
                        AssetDatabase.SaveAssets();
                        AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                        UnityEngine.Object reopened = AssetDatabase.LoadMainAssetAtPath(assetPath);
                        if (reopened == null)
                        {
                            diagnostics.Add(
                                new BridgeDiagnostic
                                {
                                    path = request.target,
                                    location = $"ops[{i}].op",
                                    detail = "apply_error",
                                    evidence = "AssetDatabase.LoadMainAssetAtPath returned null after save"
                                }
                            );
                            return BuildError(
                                "UNITY_BRIDGE_APPLY",
                                "Failed to reopen asset after save.",
                                request.target,
                                request.ops.Length,
                                executed: true,
                                applied: applied,
                                diagnostics: diagnostics.ToArray()
                            );
                        }
                        handles[AssetHandleName] = reopened;
                        saved = true;
                        applied += 1;
                        continue;
                    }

                    diagnostics.Add(
                        new BridgeDiagnostic
                        {
                            path = request.target,
                            location = $"ops[{i}].op",
                            detail = "schema_error",
                            evidence = $"unsupported asset create op '{opName}'"
                        }
                    );
                    return BuildError(
                        "UNITY_BRIDGE_SCHEMA",
                        "Invalid asset create plan.",
                        request.target,
                        request.ops.Length,
                        executed: false,
                        applied: applied,
                        diagnostics: diagnostics.ToArray()
                    );
                }

                if (assetObject == null || !saved)
                {
                    return BuildError(
                        "UNITY_BRIDGE_SCHEMA",
                        "Asset create mode requires a create_asset operation and save.",
                        request.target,
                        request.ops.Length,
                        executed: false,
                        applied: applied
                    );
                }

                return new BridgeResponse
                {
                    protocol_version = ProtocolVersion,
                    success = true,
                    severity = "info",
                    code = "SER_APPLY_OK",
                    message = "Asset create plan applied via Unity executeMethod.",
                    data = new BridgeData
                    {
                        target = request.target,
                        op_count = request.ops.Length,
                        applied = applied,
                        read_only = false,
                        executed = true,
                        protocol_version = ProtocolVersion
                    },
                    diagnostics = Array.Empty<BridgeDiagnostic>()
                };
            }
            catch (Exception ex)
            {
                diagnostics.Add(
                    new BridgeDiagnostic
                    {
                        path = request.target,
                        location = "apply",
                        detail = "exception",
                        evidence = ex.ToString()
                    }
                );
                return BuildError(
                    "UNITY_BRIDGE_APPLY_EXCEPTION",
                    $"Unexpected apply exception: {ex.Message}",
                    request.target,
                    request.ops.Length,
                    executed: true,
                    applied: applied,
                    diagnostics: diagnostics.ToArray()
                );
            }
            finally
            {
                if (!saved && AssetDatabase.LoadMainAssetAtPath(assetPath) != null)
                {
                    AssetDatabase.DeleteAsset(assetPath);
                    AssetDatabase.SaveAssets();
                    AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                }
            }
        }
    }
}
