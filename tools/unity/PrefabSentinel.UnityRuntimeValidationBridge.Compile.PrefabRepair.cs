#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;
using Object = UnityEngine.Object;

namespace PrefabSentinel
{
    public static partial class UnityRuntimeValidationBridge
    {
        internal static string[] DetectPrefabRepairs(
            CompilePreflightResult inventory)
        {
            Type udonBehaviourType = FindType("VRC.Udon.UdonBehaviour");
            if (udonBehaviourType == null)
            {
                MarkInventoryUnstable(
                    inventory,
                    "udon_behaviour_type",
                    "VRC.Udon.UdonBehaviour was not found.");
                return Array.Empty<string>();
            }

            var dependencies = new HashSet<Object>();
            for (int sceneIndex = 0;
                sceneIndex < SceneManager.sceneCount;
                sceneIndex++)
            {
                Scene scene = SceneManager.GetSceneAt(sceneIndex);
                if (!scene.IsValid() || !scene.isLoaded)
                    continue;
                foreach (GameObject root in scene.GetRootGameObjects())
                {
                    foreach (Component behaviour in root.GetComponentsInChildren(
                        udonBehaviourType,
                        true))
                    {
                        dependencies.Add(behaviour.gameObject);
                        SerializedProperty references =
                            new SerializedObject(behaviour).FindProperty(
                                "publicVariablesUnityEngineObjects");
                        if (references == null || !references.isArray)
                        {
                            MarkInventoryUnstable(
                                inventory,
                                "udon_dependency_surface",
                                "publicVariablesUnityEngineObjects is unavailable.");
                            continue;
                        }
                        for (int index = 0;
                            index < references.arraySize;
                            index++)
                        {
                            Object reference = references
                                .GetArrayElementAtIndex(index)
                                .objectReferenceValue;
                            if (reference != null)
                                dependencies.Add(reference);
                        }
                    }
                }
            }

            var paths = new List<string>();
            foreach (Object dependency in dependencies)
            {
                GameObject prefabRoot = ResolveDependencyPrefabRoot(dependency);
                if (prefabRoot == null
                    || PrefabUtility.IsPartOfImmutablePrefab(prefabRoot))
                {
                    continue;
                }
                string prefabPath = AssetDatabase.GetAssetPath(prefabRoot);
                if (!prefabPath.StartsWith(
                        "Assets/",
                        StringComparison.Ordinal)
                    || !prefabPath.EndsWith(
                        ".prefab",
                        StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }

                foreach (Component behaviour in prefabRoot.GetComponentsInChildren(
                    udonBehaviourType,
                    true))
                {
                    SerializedObject serialized =
                        new SerializedObject(behaviour);
                    SerializedProperty programSource =
                        serialized.FindProperty("programSource");
                    SerializedProperty serializedProgram =
                        serialized.FindProperty("serializedProgramAsset");
                    if (programSource == null || serializedProgram == null)
                    {
                        MarkInventoryUnstable(
                            inventory,
                            "udon_behaviour_surface",
                            "programSource or serializedProgramAsset is unavailable.");
                        continue;
                    }
                    Object serializedAsset =
                        serializedProgram.objectReferenceValue;
                    if (programSource.objectReferenceValue == null
                        && serializedAsset != null
                        && inventory.CanonicalProgramByGeneratedInstanceId
                            .ContainsKey(serializedAsset.GetInstanceID()))
                    {
                        AddUniquePath(paths, prefabPath);
                        break;
                    }
                }
            }
            paths.Sort(StringComparer.Ordinal);
            return paths.ToArray();
        }

        private static GameObject ResolveDependencyPrefabRoot(
            Object dependency)
        {
            if (dependency == null
                || !PrefabUtility.IsPartOfAnyPrefab(dependency))
            {
                return null;
            }
            if (PrefabUtility.IsPartOfPrefabAsset(dependency))
                return dependency as GameObject;
            if (!(dependency is GameObject gameObject))
                return null;
            if (PrefabUtility.IsAnyPrefabInstanceRoot(gameObject))
            {
                return PrefabUtility
                    .GetCorrespondingObjectFromOriginalSource(gameObject)
                    as GameObject;
            }
            if (!PrefabUtility.IsPartOfPrefabInstance(gameObject))
                return null;
            GameObject nearest =
                PrefabUtility.GetNearestPrefabInstanceRoot(gameObject);
            return PrefabUtility.GetCorrespondingObjectFromOriginalSource(
                nearest) as GameObject;
        }
    }
}
#endif
