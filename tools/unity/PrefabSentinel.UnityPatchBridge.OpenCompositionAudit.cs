using System;
using System.Collections.Generic;
using UnityEditor;
using UnityEngine;

namespace PrefabSentinel
{
    public static partial class UnityPatchBridge
    {
        private static CreatedResultAudit[] BuildCreatedResultAudits(
            BridgeRequest request,
            Dictionary<string, UnityEngine.Object> handles,
            GameObject savedPrefab
        )
        {
            List<CreatedResultAudit> results = new List<CreatedResultAudit>();
            for (int i = 0; i < request.ops.Length; i++)
            {
                PatchOp op = request.ops[i];
                if (!string.Equals(op.op.Trim(), "instantiate_prefab", StringComparison.Ordinal))
                {
                    continue;
                }
                string handle = NormalizeHandle(op.result);
                if (!handles.TryGetValue(handle, out UnityEngine.Object value))
                {
                    throw new InvalidOperationException(
                        $"Created result handle '${handle}' was not retained for audit."
                    );
                }
                GameObject instance = value as GameObject;
                if (instance == null)
                {
                    throw new InvalidOperationException(
                        $"Created result handle '${handle}' is not a GameObject."
                    );
                }

                string locator = BuildPostSaveSymbolPath(instance.transform);
                if (!TryResolveHierarchyPathWithResolver(
                        savedPrefab,
                        locator,
                        out Transform persistedTransform,
                        out string resolveError))
                {
                    throw new InvalidOperationException(
                        $"Created result handle '${handle}' could not be resolved "
                        + $"from the saved Prefab: {resolveError}"
                    );
                }
                results.Add(
                    BuildCreatedResultAudit(
                        handle,
                        persistedTransform.gameObject,
                        op
                    )
                );
            }
            return results.ToArray();
        }

        private static CreatedResultAudit BuildCreatedResultAudit(
            string handle,
            GameObject persistedInstance,
            PatchOp op
        )
        {
            if (!TryResolveAssetPath(
                    op.prefab,
                    false,
                    out string sourcePath,
                    out string sourceError))
            {
                throw new InvalidOperationException(sourceError);
            }
            GlobalObjectId gameObjectId =
                GlobalObjectId.GetGlobalObjectIdSlow(persistedInstance);
            GlobalObjectId transformId =
                GlobalObjectId.GetGlobalObjectIdSlow(persistedInstance.transform);
            return new CreatedResultAudit
            {
                handle = handle,
                symbol_path = BuildPostSaveSymbolPath(
                    persistedInstance.transform
                ),
                game_object_file_id = gameObjectId.targetObjectId.ToString(),
                transform_file_id = transformId.targetObjectId.ToString(),
                source_asset_path = sourcePath,
                source_asset_guid = AssetDatabase.AssetPathToGUID(sourcePath),
                overrides = BuildPropertyOverrideAudits(persistedInstance)
            };
        }

        private static string BuildPostSaveSymbolPath(Transform target)
        {
            List<string> segments = new List<string>();
            Transform current = target;
            while (current != null)
            {
                string segment = current.name;
                Transform parent = current.parent;
                if (parent != null)
                {
                    int sameNameIndex = 0;
                    int sameNameCount = 0;
                    for (int i = 0; i < parent.childCount; i++)
                    {
                        Transform sibling = parent.GetChild(i);
                        if (!string.Equals(
                                sibling.name,
                                current.name,
                                StringComparison.Ordinal))
                        {
                            continue;
                        }
                        if (sibling == current)
                        {
                            sameNameIndex = sameNameCount;
                        }
                        sameNameCount += 1;
                    }
                    if (sameNameCount > 1)
                    {
                        segment = $"{segment}#{sameNameIndex}";
                    }
                }
                segments.Add(segment);
                current = parent;
            }
            segments.Reverse();
            return string.Join("/", segments.ToArray());
        }

        private static PropertyOverrideAudit[] BuildPropertyOverrideAudits(
            GameObject persistedInstance
        )
        {
            PropertyModification[] modifications =
                PrefabUtility.GetPropertyModifications(persistedInstance);
            if (modifications == null)
            {
                return Array.Empty<PropertyOverrideAudit>();
            }
            List<PropertyOverrideAudit> results = new List<PropertyOverrideAudit>();
            for (int i = 0; i < modifications.Length; i++)
            {
                PropertyModification modification = modifications[i];
                if (modification == null || modification.target == null)
                {
                    throw new InvalidOperationException(
                        "Prefab property modification has no target."
                    );
                }
                results.Add(
                    new PropertyOverrideAudit
                    {
                        component = modification.target.GetType().FullName,
                        property_path = modification.propertyPath
                    }
                );
            }
            results.Sort(
                (left, right) =>
                {
                    int componentOrder = string.CompareOrdinal(
                        left.component,
                        right.component
                    );
                    return componentOrder != 0
                        ? componentOrder
                        : string.CompareOrdinal(
                            left.property_path,
                            right.property_path
                        );
                }
            );
            return results.ToArray();
        }
    }
}
