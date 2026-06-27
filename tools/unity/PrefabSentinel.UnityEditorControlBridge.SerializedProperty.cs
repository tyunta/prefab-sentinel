using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

// SerializedProperty action handlers stay separate from target, traversal, write, and payload helpers.
namespace PrefabSentinel
{
    public static partial class UnityEditorControlBridge
    {
        private static EditorControlResponse HandleSerializedPropertyRead(
            EditorControlRequest request)
        {
            EditorControlResponse inputError = ValidateSerializedPropertyAddress(
                request, requirePropertyPath: true);
            if (inputError != null) return inputError;

            SerializedPropertyTarget target;
            EditorControlResponse targetError = ResolveSerializedPropertyTarget(
                request, out target);
            if (targetError != null) return targetError;

            SerializedObject serializedObject = new SerializedObject(target.TargetObject);
            SerializedProperty property =
                serializedObject.FindProperty(request.property_path);
            if (property == null)
                return BuildPropertyNotFoundError(
                    serializedObject, request.property_path, target.State);

            SerializedPropertyTraversalOptions childOptions =
                SerializedPropertyTraversalOptions.Parse(1, 50, string.Empty);
            string json = BuildSerializedPropertyJson(
                property, childOptions, target.State);
            return BuildSuccess(
                "EDITOR_CTRL_SERIALIZED_PROPERTY_READ_OK",
                "Serialized property read.",
                new EditorControlData
                {
                    read_only = true,
                    executed = false,
                    hierarchy_path = request.hierarchy_path,
                    serialized_property_json = json,
                });
        }

        private static EditorControlResponse HandleSerializedPropertyList(
            EditorControlRequest request)
        {
            EditorControlResponse inputError = ValidateSerializedPropertyAddress(
                request, requirePropertyPath: false);
            if (inputError != null) return inputError;

            SerializedPropertyTraversalOptions options =
                SerializedPropertyTraversalOptions.Parse(
                    request.depth, request.cap, request.cursor);
            if (!options.Success)
                return BuildError(
                    options.ErrorCode,
                    "Invalid serialized property traversal options.");

            SerializedPropertyTarget target;
            EditorControlResponse targetError = ResolveSerializedPropertyTarget(
                request, out target);
            if (targetError != null) return targetError;

            SerializedObject serializedObject = new SerializedObject(target.TargetObject);
            SerializedProperty root = null;
            if (!string.IsNullOrEmpty(request.root_property_path))
            {
                root = serializedObject.FindProperty(request.root_property_path);
                if (root == null)
                    return BuildPropertyNotFoundError(
                        serializedObject, request.root_property_path, target.State);
            }

            SerializedProperty iterator = root != null
                ? root.Copy()
                : serializedObject.GetIterator();
            List<string> entries = new List<string>();
            bool truncated;
            int nextCursor;
            CollectSerializedPropertyList(
                iterator, root, options, target.State,
                entries, out truncated, out nextCursor);

            string json = BuildSerializedPropertyListJson(
                entries, truncated, nextCursor, target.State);
            return BuildSuccess(
                "EDITOR_CTRL_SERIALIZED_PROPERTY_LIST_OK",
                "Serialized properties listed.",
                new EditorControlData
                {
                    read_only = true,
                    executed = false,
                    hierarchy_path = request.hierarchy_path,
                    next_cursor = truncated
                        ? nextCursor.ToString(CultureInfo.InvariantCulture)
                        : string.Empty,
                    serialized_property_json = json,
                });
        }

        private static EditorControlResponse HandleSerializedPropertyWrite(
            EditorControlRequest request)
        {
            EditorControlResponse inputError = ValidateSerializedPropertyAddress(
                request, requirePropertyPath: true);
            if (inputError != null) return inputError;

            SerializedPropertyValueIntent intent =
                SerializedPropertyValueIntent.Parse(request);
            if (!intent.Success)
                return BuildError(intent.ErrorCode, "Invalid serialized property value intent.");

            if (request.confirm && string.IsNullOrWhiteSpace(request.change_reason))
                return BuildError(
                    "EDITOR_CTRL_SERIALIZED_PROPERTY_CHANGE_REASON_REQUIRED",
                    "change_reason is required for confirmed serialized property writes.");

            SerializedPropertyTarget target;
            EditorControlResponse targetError = ResolveSerializedPropertyTarget(
                request, out target);
            if (targetError != null) return targetError;

            SerializedObject serializedObject = new SerializedObject(target.TargetObject);
            SerializedProperty property =
                serializedObject.FindProperty(request.property_path);
            if (property == null)
                return BuildPropertyNotFoundError(
                    serializedObject, request.property_path, target.State);

            SerializedPropertyWritePlan preview =
                ApplySerializedPropertyValueIntent(property, intent, request, false);
            if (!preview.Success)
                return BuildError(
                    preview.ErrorCode,
                    preview.ErrorMessage,
                    new EditorControlData
                    {
                        serialized_property_json = BuildWriteResultJson(
                            preview, target.State, "not_attempted", false, "{}"),
                    });

            if (!request.confirm)
                return BuildSuccess(
                    "EDITOR_CTRL_SERIALIZED_PROPERTY_DRY_RUN_OK",
                    "Serialized property dry-run validated.",
                    new EditorControlData
                    {
                        read_only = false,
                        executed = false,
                        saved = false,
                        hierarchy_path = request.hierarchy_path,
                        serialized_property_json = BuildWriteResultJson(
                            preview, target.State, "planned", false, "{}"),
                    });

            if (!preview.WouldChange)
                return BuildSuccess(
                    "EDITOR_CTRL_SERIALIZED_PROPERTY_NO_CHANGE",
                    "Serialized property already has the requested value.",
                    new EditorControlData
                    {
                        read_only = false,
                        executed = false,
                        saved = false,
                        hierarchy_path = request.hierarchy_path,
                        serialized_property_json = BuildWriteResultJson(
                            preview, target.State, "not_required", false, "{}"),
                    });

            Undo.RecordObject(target.TargetObject, request.change_reason.Trim());
            SerializedPropertyWritePlan applied =
                ApplySerializedPropertyValueIntent(property, intent, request, true);
            if (!applied.Success)
                return BuildError(
                    applied.ErrorCode,
                    applied.ErrorMessage,
                    new EditorControlData
                    {
                        serialized_property_json = BuildWriteResultJson(
                            applied, target.State, "not_attempted", false, "{}"),
                    });

            serializedObject.ApplyModifiedProperties();
            MarkSerializedPropertyTargetDirty(target);
            string overrideJson = RecordSerializedPropertyPrefabOverride(target);
            UdonSharpSyncEvidence sync = BuildUdonSharpSyncStatus(target);

            EditorControlResponse response = BuildSuccess(
                "EDITOR_CTRL_SERIALIZED_PROPERTY_WRITE_OK",
                "Serialized property written.",
                new EditorControlData
                {
                    read_only = false,
                    executed = true,
                    saved = false,
                    hierarchy_path = request.hierarchy_path,
                    serialized_property_json = BuildWriteResultJson(
                        applied, target.State, sync.status, true, overrideJson),
                });
            if (sync.warning)
            {
                response.severity = "warning";
                response.diagnostics = new[]
                {
                    new EditorControlDiagnostic
                    {
                        code = "EDITOR_CTRL_SERIALIZED_PROPERTY_UDON_SYNC_WARNING",
                        severity = "warning",
                        detail = sync.detail,
                        evidence = "sync_status",
                    },
                };
            }
            return response;
        }
    }
}
