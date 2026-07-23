using System;
using System.Collections.Generic;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace PrefabSentinel
{
    public static partial class UnityEditorControlBridge
    {
        [Serializable]
        public sealed class EditorStateSnapshot
        {
            public string state_source = string.Empty;
            public bool is_playing = false;
            public bool is_will_change_playmode = false;
            public bool is_compiling = false;
            public bool is_building_player = false;
            public bool has_unsaved_changes = false;
            public string active_stage_kind = string.Empty;
            public string active_scene_path = string.Empty;
            public string active_scene_name = string.Empty;
            public string prefab_stage_asset_path = string.Empty;
            public string prefab_stage_root_name = string.Empty;
            public bool prefab_stage_is_dirty = false;
            public string[] dirty_scene_paths = Array.Empty<string>();
            public string[] dirty_prefab_paths = Array.Empty<string>();
            public string[] dirty_material_paths = Array.Empty<string>();
            public string[] dirty_asset_paths = Array.Empty<string>();
            public EditorSceneStatus[] open_scenes = Array.Empty<EditorSceneStatus>();
        }

        [Serializable]
        public sealed class EditorSceneStatus
        {
            public string path = string.Empty;
            public string name = string.Empty;
            public bool is_dirty = false;
        }

        private static EditorControlDiagnostic LimitedEditorStateDiagnostic(
            string location,
            Exception ex)
        {
            return new EditorControlDiagnostic
            {
                code = "EDITOR_STATE_ENUMERATION_LIMITED",
                severity = "warning",
                location = location,
                detail = ex.Message,
            };
        }

        private static EditorSceneStatus[] CollectOpenSceneStatuses(
            List<EditorControlDiagnostic> diagnostics)
        {
            try
            {
                var scenes = new List<EditorSceneStatus>();
                for (int i = 0; i < EditorSceneManager.sceneCount; i++)
                {
                    var scene = EditorSceneManager.GetSceneAt(i);
                    scenes.Add(new EditorSceneStatus
                    {
                        path = scene.path ?? string.Empty,
                        name = scene.name ?? string.Empty,
                        is_dirty = scene.isDirty,
                    });
                }
                return scenes.ToArray();
            }
            catch (Exception ex)
            {
                diagnostics.Add(LimitedEditorStateDiagnostic("open_scenes", ex));
                return Array.Empty<EditorSceneStatus>();
            }
        }

        private static void PopulateActiveSceneStatus(
            EditorStateSnapshot snapshot,
            List<EditorControlDiagnostic> diagnostics)
        {
            try
            {
                var activeScene = EditorSceneManager.GetActiveScene();
                snapshot.active_scene_path = activeScene.path ?? string.Empty;
                snapshot.active_scene_name = activeScene.name ?? string.Empty;
            }
            catch (Exception ex)
            {
                diagnostics.Add(LimitedEditorStateDiagnostic("active_scene", ex));
            }
        }

        private static void PopulatePrefabStageStatus(
            EditorStateSnapshot snapshot,
            List<EditorControlDiagnostic> diagnostics)
        {
            try
            {
                var stage = PrefabStageUtility.GetCurrentPrefabStage();
                if (EditorApplication.isPlayingOrWillChangePlaymode)
                {
                    snapshot.active_stage_kind = "play_mode";
                }
                else if (stage != null)
                {
                    snapshot.active_stage_kind = "prefab_stage";
                    snapshot.prefab_stage_asset_path = stage.assetPath ?? string.Empty;
                    snapshot.prefab_stage_root_name = stage.prefabContentsRoot != null
                        ? stage.prefabContentsRoot.name
                        : string.Empty;
                    snapshot.prefab_stage_is_dirty = stage.scene.isDirty;
                }
                else
                {
                    snapshot.active_stage_kind = "scene";
                }
            }
            catch (Exception ex)
            {
                diagnostics.Add(LimitedEditorStateDiagnostic("prefab_stage", ex));
                if (string.IsNullOrEmpty(snapshot.active_stage_kind))
                    snapshot.active_stage_kind = "scene";
            }
        }

        private static void PopulateDirtyIdentityStatus(
            EditorStateSnapshot snapshot,
            List<EditorControlDiagnostic> diagnostics)
        {
            snapshot.dirty_scene_paths = CollectDirtyScenePaths(diagnostics);
            snapshot.dirty_prefab_paths = CollectDirtyPrefabPaths(diagnostics);
            snapshot.dirty_material_paths = CollectDirtyMaterialPaths(diagnostics);
            snapshot.dirty_asset_paths = CollectDirtyAssetPaths(diagnostics);
            snapshot.has_unsaved_changes =
                snapshot.has_unsaved_changes
                || snapshot.dirty_scene_paths.Length > 0
                || snapshot.dirty_prefab_paths.Length > 0
                || snapshot.dirty_material_paths.Length > 0
                || snapshot.dirty_asset_paths.Length > 0;
        }

        private static EditorControlResponse HandleGetEditorState()
        {
            var diagnostics = new List<EditorControlDiagnostic>();
            var snapshot = new EditorStateSnapshot
            {
                state_source = "live_editor",
                is_playing = EditorApplication.isPlaying,
                is_will_change_playmode = EditorApplication.isPlayingOrWillChangePlaymode,
                is_compiling = EditorApplication.isCompiling,
                is_building_player = BuildPipeline.isBuildingPlayer,
                has_unsaved_changes = HasUnsavedEditorChanges(),
            };
            PopulateActiveSceneStatus(snapshot, diagnostics);
            PopulatePrefabStageStatus(snapshot, diagnostics);
            PopulateDirtyIdentityStatus(snapshot, diagnostics);
            snapshot.open_scenes = CollectOpenSceneStatuses(diagnostics);

            var response = BuildSuccess(
                "EDITOR_CTRL_EDITOR_STATE_OK",
                "Editor state snapshot captured.",
                new EditorControlData
                {
                    executed = true,
                    editor_state = snapshot,
                });
            response.diagnostics = diagnostics.ToArray();
            if (diagnostics.Count > 0) response.severity = "warning";
            return response;
        }

        private static bool HasUnsavedEditorChanges()
        {
            var stage = PrefabStageUtility.GetCurrentPrefabStage();
            if (stage != null)
                return stage.scene.isDirty;

            for (int i = 0; i < EditorSceneManager.sceneCount; i++)
            {
                if (EditorSceneManager.GetSceneAt(i).isDirty)
                    return true;
            }
            return false;
        }

        private static string[] CollectDirtyScenePaths(
            List<EditorControlDiagnostic> diagnostics)
        {
            try
            {
                var paths = new HashSet<string>(StringComparer.Ordinal);
                for (int i = 0; i < EditorSceneManager.sceneCount; i++)
                {
                    var scene = EditorSceneManager.GetSceneAt(i);
                    if (scene.isDirty && !string.IsNullOrEmpty(scene.path))
                        paths.Add(scene.path);
                }
                return SortedPaths(paths);
            }
            catch (Exception ex)
            {
                diagnostics.Add(LimitedEditorStateDiagnostic("dirty_scene_paths", ex));
                return Array.Empty<string>();
            }
        }

        private static string[] CollectDirtyPrefabPaths(
            List<EditorControlDiagnostic> diagnostics)
        {
            try
            {
                var paths = new HashSet<string>(StringComparer.Ordinal);
                var stage = PrefabStageUtility.GetCurrentPrefabStage();
                if (stage != null && stage.scene.isDirty && !string.IsNullOrEmpty(stage.assetPath))
                    paths.Add(stage.assetPath);

                foreach (var gameObject in Resources.FindObjectsOfTypeAll<GameObject>())
                {
                    if (!EditorUtility.IsDirty(gameObject))
                        continue;
                    var path = AssetDatabase.GetAssetPath(gameObject);
                    if (!string.IsNullOrEmpty(path) && path.EndsWith(".prefab", StringComparison.OrdinalIgnoreCase))
                        paths.Add(path);
                }
                return SortedPaths(paths);
            }
            catch (Exception ex)
            {
                diagnostics.Add(LimitedEditorStateDiagnostic("dirty_prefab_paths", ex));
                return Array.Empty<string>();
            }
        }

        private static string[] CollectDirtyMaterialPaths(
            List<EditorControlDiagnostic> diagnostics)
        {
            try
            {
                var paths = new HashSet<string>(StringComparer.Ordinal);
                foreach (var material in Resources.FindObjectsOfTypeAll<Material>())
                {
                    if (!EditorUtility.IsDirty(material))
                        continue;
                    var path = AssetDatabase.GetAssetPath(material);
                    if (!string.IsNullOrEmpty(path) && path.EndsWith(".mat", StringComparison.OrdinalIgnoreCase))
                        paths.Add(path);
                }
                return SortedPaths(paths);
            }
            catch (Exception ex)
            {
                diagnostics.Add(LimitedEditorStateDiagnostic("dirty_material_paths", ex));
                return Array.Empty<string>();
            }
        }

        private static string[] CollectDirtyAssetPaths(
            List<EditorControlDiagnostic> diagnostics)
        {
            try
            {
                var paths = new HashSet<string>(StringComparer.Ordinal);
                foreach (var asset in Resources.FindObjectsOfTypeAll<UnityEngine.Object>())
                {
                    if (!EditorUtility.IsDirty(asset))
                        continue;
                    var path = AssetDatabase.GetAssetPath(asset);
                    if (IsDirtyAssetIdentityPath(path))
                        paths.Add(path);
                }
                return SortedPaths(paths);
            }
            catch (Exception ex)
            {
                diagnostics.Add(LimitedEditorStateDiagnostic("dirty_asset_paths", ex));
                return Array.Empty<string>();
            }
        }

        private static bool IsDirtyAssetIdentityPath(string path)
        {
            return !string.IsNullOrEmpty(path)
                && path.StartsWith("Assets/", StringComparison.Ordinal)
                && !path.EndsWith(".unity", StringComparison.OrdinalIgnoreCase)
                && !path.EndsWith(".prefab", StringComparison.OrdinalIgnoreCase)
                && !path.EndsWith(".mat", StringComparison.OrdinalIgnoreCase);
        }

        private static string[] SortedPaths(HashSet<string> paths)
        {
            var result = new List<string>(paths);
            result.Sort(StringComparer.Ordinal);
            return result.ToArray();
        }
    }
}
