using System;
using UnityEditor;

namespace PrefabSentinel
{
    public static partial class UnityEditorControlBridge
    {
        private static EditorControlResponse HandleMoveAsset(
            EditorControlRequest request)
        {
            AssetOpsMovePathValidationResult validation =
                AssetOpsPathValidation.ValidateMoveAssetPaths(
                    request.source_asset_path,
                    request.destination_asset_path);
            if (!validation.IsValid)
            {
                return BuildError(
                    validation.Code,
                    $"Invalid asset move path: {validation.Reason}",
                    MoveAssetData(request, validation, string.Empty, null));
            }

            string beforeGuid = AssetDatabase.AssetPathToGUID(
                validation.SourcePath);
            EditorControlResponse stateError =
                ValidateMoveAssetDatabaseState(
                    request, validation, beforeGuid, out UnityEngine.Object asset);
            if (stateError != null)
            {
                return stateError;
            }

            if (!request.confirm)
            {
                return BuildSuccess(
                    "ASSET_MOVE_DRY_RUN_OK",
                    $"Asset can be moved to {validation.DestinationPath}.",
                    MoveAssetData(request, validation, beforeGuid, asset));
            }

            return MoveAssetConfirmed(request, validation, beforeGuid, asset);
        }

        private static EditorControlResponse ValidateMoveAssetDatabaseState(
            EditorControlRequest request,
            AssetOpsMovePathValidationResult validation,
            string beforeGuid,
            out UnityEngine.Object asset)
        {
            asset = null;
            if (string.IsNullOrEmpty(beforeGuid))
            {
                EditorControlData data =
                    MoveAssetData(request, validation, beforeGuid, asset);
                data.meta_exists = MetaFileExists(validation.SourcePath);
                return BuildError(
                    "ASSET_SOURCE_NOT_FOUND",
                    $"Source asset not found: {validation.SourcePath}.",
                    data);
            }
            if (AssetDatabase.IsValidFolder(validation.SourcePath))
            {
                return BuildError(
                    "ASSET_SOURCE_IS_FOLDER",
                    $"Source path is a folder: {validation.SourcePath}.",
                    MoveAssetData(request, validation, beforeGuid, asset));
            }

            asset = AssetDatabase.LoadMainAssetAtPath(validation.SourcePath);
            if (asset == null)
            {
                return BuildError(
                    "ASSET_SOURCE_LOAD_FAILED",
                    $"Failed to load source asset: {validation.SourcePath}.",
                    MoveAssetData(request, validation, beforeGuid, asset));
            }

            string destinationGuid =
                AssetDatabase.AssetPathToGUID(validation.DestinationPath);
            if (!string.IsNullOrEmpty(destinationGuid))
            {
                EditorControlData data =
                    MoveAssetData(request, validation, beforeGuid, asset);
                data.after_guid = destinationGuid;
                return BuildError(
                    "ASSET_DESTINATION_EXISTS",
                    $"Destination already exists: {validation.DestinationPath}.",
                    data);
            }
            if (MetaFileExists(validation.DestinationPath))
            {
                EditorControlData data =
                    MoveAssetData(request, validation, beforeGuid, asset);
                data.meta_exists = true;
                return BuildError(
                    "ASSET_DESTINATION_META_EXISTS",
                    $"Destination meta file already exists: {validation.DestinationPath}.meta.",
                    data);
            }

            string parentPath = ParentPath(validation.DestinationPath);
            string parentGuid = AssetDatabase.AssetPathToGUID(parentPath);
            if (string.IsNullOrEmpty(parentGuid) && parentPath != "Assets")
            {
                return BuildError(
                    "ASSET_DESTINATION_PARENT_NOT_FOUND",
                    $"Destination parent folder not found: {parentPath}.",
                    MoveAssetData(request, validation, beforeGuid, asset));
            }
            if (!AssetDatabase.IsValidFolder(parentPath))
            {
                return BuildError(
                    "ASSET_DESTINATION_PARENT_NOT_FOLDER",
                    $"Destination parent path is not a folder: {parentPath}.",
                    MoveAssetData(request, validation, beforeGuid, asset));
            }

            return null;
        }

        private static EditorControlResponse MoveAssetConfirmed(
            EditorControlRequest request,
            AssetOpsMovePathValidationResult validation,
            string beforeGuid,
            UnityEngine.Object asset)
        {
            EditorControlData data =
                MoveAssetData(request, validation, beforeGuid, asset);
            data.would_move = false;
            data.dirty_before = EditorUtility.IsDirty(asset);

            string unityError =
                AssetDatabase.MoveAsset(
                    validation.SourcePath, validation.DestinationPath);
            if (!string.IsNullOrEmpty(unityError))
            {
                data.unity_error = unityError;
                return BuildError(
                    "ASSET_MOVE_FAILED",
                    $"AssetDatabase.MoveAsset failed: {unityError}",
                    data);
            }

            data.moved = true;
            if (data.name_changed)
            {
                asset.name = validation.DestinationStem;
                EditorUtility.SetDirty(asset);
            }

            EditorControlResponse saveRefreshError =
                SaveAndRefreshMove(validation.DestinationPath, data);
            if (saveRefreshError != null)
            {
                return saveRefreshError;
            }

            data.after_guid =
                AssetDatabase.AssetPathToGUID(validation.DestinationPath);
            UnityEngine.Object movedAsset =
                AssetDatabase.LoadMainAssetAtPath(validation.DestinationPath);
            data.dirty_after =
                movedAsset != null && EditorUtility.IsDirty(movedAsset);
            data.guid_preserved =
                !string.IsNullOrEmpty(beforeGuid)
                && data.after_guid == beforeGuid;

            if (string.IsNullOrEmpty(data.after_guid)
                || movedAsset == null
                || !data.guid_preserved)
            {
                return WithPartialSideEffectDiagnostic(BuildError(
                    "ASSET_MOVE_POSTCHECK_FAILED",
                    $"Moved asset failed postcheck at {validation.DestinationPath}.",
                    data));
            }
            if (data.dirty_after)
            {
                return WithPartialSideEffectDiagnostic(BuildError(
                    "ASSET_MOVE_DIRTY_POSTCHECK_FAILED",
                    $"Moved asset remained dirty after save: {validation.DestinationPath}.",
                    data));
            }

            return BuildSuccess(
                "ASSET_MOVE_OK",
                $"Moved asset to {validation.DestinationPath}.",
                data);
        }

        private static EditorControlResponse SaveAndRefreshMove(
            string destinationPath,
            EditorControlData data)
        {
            try
            {
                data.phase = "save";
                AssetDatabase.SaveAssets();
                data.saved = true;
                data.phase = "refresh";
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                data.refreshed = true;
                data.phase = "postcheck";
                return null;
            }
            catch (Exception ex)
            {
                data.state_unknown = true;
                data.exception_type = ex.GetType().Name;
                data.exception_message = ex.Message;
                return WithPartialSideEffectDiagnostic(BuildError(
                    "ASSET_MOVE_SAVE_OR_REFRESH_FAILED",
                    $"Failed to save or refresh moved asset: {destinationPath}.",
                    data));
            }
        }

        private static EditorControlData MoveAssetData(
            EditorControlRequest request,
            AssetOpsMovePathValidationResult validation,
            string beforeGuid,
            UnityEngine.Object asset)
        {
            string newName = validation.DestinationStem;
            string oldName = asset == null ? string.Empty : asset.name;
            return new EditorControlData
            {
                read_only = !request.confirm,
                executed = request.confirm,
                source_asset_path = validation.SourcePath,
                destination_asset_path = validation.DestinationPath,
                unity_type = asset == null ? string.Empty : asset.GetType().Name,
                before_guid = beforeGuid,
                after_guid = string.Empty,
                guid_preserved = false,
                would_move = !request.confirm,
                moved = false,
                dry_run = !request.confirm,
                saved = false,
                refreshed = false,
                dirty_before = asset != null && EditorUtility.IsDirty(asset),
                dirty_after = asset != null && EditorUtility.IsDirty(asset),
                old_name = oldName,
                new_name = newName,
                name_changed =
                    !string.Equals(oldName, newName, StringComparison.Ordinal),
            };
        }
    }
}
