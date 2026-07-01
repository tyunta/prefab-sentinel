using System;
using UnityEditor;
using UnityEngine;

namespace PrefabSentinel
{
    public static partial class UnityEditorControlBridge
    {
        private static EditorControlResponse HandleCreateGeneratedAsset(
            EditorControlRequest request)
        {
            if (request.asset_type != GeneratedAssetType)
            {
                return BuildError(
                    "UNSUPPORTED_GENERATED_ASSET_TYPE",
                    $"Unsupported generated asset type: {request.asset_type}");
            }

            AssetOpsPathValidationResult validation =
                AssetOpsPathValidation.ValidateGeneratedAssetPath(
                    request.asset_path);
            if (!validation.IsValid)
            {
                return BuildError(
                    validation.Code,
                    $"Invalid generated asset path: {validation.Reason}",
                    CreateAssetData(request, validation.Path, validation.Stem));
            }

            EditorControlResponse stateError =
                ValidateCreateAssetDatabaseState(request, validation);
            if (stateError != null)
            {
                return stateError;
            }

            if (!request.confirm)
            {
                return BuildSuccess(
                    "GENERATED_ASSET_DRY_RUN_OK",
                    $"RenderTexture can be created at {validation.Path}.",
                    CreateAssetData(request, validation.Path, validation.Stem));
            }

            return CreateGeneratedAssetConfirmed(request, validation);
        }

        private static EditorControlResponse ValidateCreateAssetDatabaseState(
            EditorControlRequest request,
            AssetOpsPathValidationResult validation)
        {
            string existingGuid = AssetDatabase.AssetPathToGUID(validation.Path);
            if (!string.IsNullOrEmpty(existingGuid))
            {
                EditorControlData data =
                    CreateAssetData(request, validation.Path, validation.Stem);
                data.guid = existingGuid;
                return BuildError(
                    "GENERATED_ASSET_DESTINATION_EXISTS",
                    $"Asset already exists at {validation.Path}.",
                    data);
            }

            if (MetaFileExists(validation.Path))
            {
                EditorControlData data =
                    CreateAssetData(request, validation.Path, validation.Stem);
                data.meta_exists = true;
                return BuildError(
                    "GENERATED_ASSET_DESTINATION_META_EXISTS",
                    $"Asset meta file already exists at {validation.Path}.meta.",
                    data);
            }

            string parentPath = ParentPath(validation.Path);
            string parentGuid = AssetDatabase.AssetPathToGUID(parentPath);
            if (string.IsNullOrEmpty(parentGuid) && parentPath != "Assets")
            {
                return BuildError(
                    "GENERATED_ASSET_PARENT_NOT_FOUND",
                    $"Parent folder not found: {parentPath}.",
                    CreateAssetData(request, validation.Path, validation.Stem));
            }
            if (!AssetDatabase.IsValidFolder(parentPath))
            {
                return BuildError(
                    "GENERATED_ASSET_PARENT_NOT_FOLDER",
                    $"Parent path is not a folder: {parentPath}.",
                    CreateAssetData(request, validation.Path, validation.Stem));
            }

            return null;
        }

        private static EditorControlResponse CreateGeneratedAssetConfirmed(
            EditorControlRequest request,
            AssetOpsPathValidationResult validation)
        {
            EditorControlData data =
                CreateAssetData(request, validation.Path, validation.Stem);
            data.would_create = false;

            try
            {
                RenderTextureFormat format =
                    ParseEnum<RenderTextureFormat>(request.parameters.format);
                RenderTextureReadWrite readWrite =
                    ParseEnum<RenderTextureReadWrite>(
                        request.parameters.read_write);
                FilterMode filterMode =
                    ParseEnum<FilterMode>(request.parameters.filter_mode);
                TextureWrapMode wrapMode =
                    ParseEnum<TextureWrapMode>(request.parameters.wrap_mode);

                var renderTexture = new RenderTexture(
                    request.parameters.width,
                    request.parameters.height,
                    request.parameters.depth,
                    format,
                    readWrite);
                renderTexture.name = validation.Stem;
                renderTexture.filterMode = filterMode;
                renderTexture.useMipMap = request.parameters.mip_map;
                renderTexture.wrapMode = wrapMode;
                AssetDatabase.CreateAsset(renderTexture, validation.Path);
                data.created = true;
            }
            catch (Exception ex)
            {
                data.state_unknown = true;
                data.exception_type = ex.GetType().Name;
                data.exception_message = ex.Message;
                return WithPartialSideEffectDiagnostic(BuildError(
                    "GENERATED_ASSET_CREATE_FAILED",
                    $"Failed to create RenderTexture at {validation.Path}.",
                    data));
            }

            EditorControlResponse saveRefreshError =
                SaveAndRefreshCreate(validation.Path, data);
            if (saveRefreshError != null)
            {
                return saveRefreshError;
            }

            string guid = AssetDatabase.AssetPathToGUID(validation.Path);
            UnityEngine.Object asset =
                AssetDatabase.LoadMainAssetAtPath(validation.Path);
            data.guid = guid;
            data.dirty_after = asset != null && EditorUtility.IsDirty(asset);

            if (string.IsNullOrEmpty(guid) || !(asset is RenderTexture))
            {
                return WithPartialSideEffectDiagnostic(BuildError(
                    "GENERATED_ASSET_POSTCHECK_FAILED",
                    $"Created asset failed postcheck at {validation.Path}.",
                    data));
            }
            if (data.dirty_after)
            {
                return WithPartialSideEffectDiagnostic(BuildError(
                    "GENERATED_ASSET_DIRTY_POSTCHECK_FAILED",
                    $"Created asset remained dirty after save: {validation.Path}.",
                    data));
            }

            return BuildSuccess(
                "GENERATED_ASSET_CREATE_OK",
                $"Created RenderTexture at {validation.Path}.",
                data);
        }

        private static EditorControlResponse SaveAndRefreshCreate(
            string assetPath,
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
                    "GENERATED_ASSET_SAVE_OR_REFRESH_FAILED",
                    $"Failed to save or refresh generated asset: {assetPath}.",
                    data));
            }
        }

        private static EditorControlData CreateAssetData(
            EditorControlRequest request,
            string assetPath,
            string name)
        {
            return new EditorControlData
            {
                read_only = !request.confirm,
                executed = request.confirm,
                asset_type = request.asset_type,
                unity_type = GeneratedAssetUnityType,
                asset_path = assetPath,
                would_create = !request.confirm,
                created = false,
                dry_run = !request.confirm,
                saved = false,
                refreshed = false,
                dirty_before = false,
                dirty_after = false,
                name = name,
                applied_parameters = request.parameters,
            };
        }
    }
}
