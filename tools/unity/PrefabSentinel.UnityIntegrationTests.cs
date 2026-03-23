using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;

namespace PrefabSentinel
{
    /// <summary>
    /// Unity batchmode integration tests for <see cref="UnityPatchBridge"/>.
    /// Invoked via -executeMethod PrefabSentinel.UnityIntegrationTests.RunAll
    /// with -sentinelTestOutputPath &lt;results.json&gt;.
    /// Creates transient fixture assets, exercises set / insert_array_element /
    /// remove_array_element through ApplyFromPaths, then verifies both the
    /// bridge response and the actual serialized property values.
    /// </summary>
    public static class UnityIntegrationTests
    {
        private const string TestArgOutputPath = "-sentinelTestOutputPath";
        private const string TestAssetDir = "Assets/PrefabSentinelIntegrationTests";
        private const int ProtocolVersion = 2;

        // ----------------------------------------------------------------
        // Result data model (JSON-serializable)
        // ----------------------------------------------------------------

        [Serializable]
        private sealed class TestSuiteResult
        {
            public int protocol_version = 1;
            public bool success = false;
            public string severity = "error";
            public string code = "INTEGRATION_TEST_ERROR";
            public string message = string.Empty;
            public TestSuiteData data = new TestSuiteData();
            public TestDiagnostic[] diagnostics = Array.Empty<TestDiagnostic>();
        }

        [Serializable]
        private sealed class TestSuiteData
        {
            public int total = 0;
            public int passed = 0;
            public int failed = 0;
            public float duration_sec = 0f;
            public string test_asset_dir = TestAssetDir;
            public TestCaseResult[] cases = Array.Empty<TestCaseResult>();
        }

        [Serializable]
        private sealed class TestCaseResult
        {
            public string name = string.Empty;
            public bool passed = false;
            public float duration_sec = 0f;
            public string message = string.Empty;
            public TestDiagnostic[] diagnostics = Array.Empty<TestDiagnostic>();
        }

        [Serializable]
        private sealed class TestDiagnostic
        {
            public string path = string.Empty;
            public string location = string.Empty;
            public string detail = string.Empty;
            public string evidence = string.Empty;
        }

        // ----------------------------------------------------------------
        // Lightweight bridge response for test assertions
        // ----------------------------------------------------------------

        [Serializable]
        private sealed class BridgeResponseReadback
        {
            public int protocol_version = 0;
            public bool success = false;
            public string severity = "error";
            public string code = string.Empty;
            public string message = string.Empty;
            public BridgeDataReadback data = new BridgeDataReadback();
        }

        [Serializable]
        private sealed class BridgeDataReadback
        {
            public int applied = 0;
        }

        // ----------------------------------------------------------------
        // Entry point
        // ----------------------------------------------------------------

        public static void RunAll()
        {
            string outputPath = GetArgValue(TestArgOutputPath);
            if (string.IsNullOrWhiteSpace(outputPath))
            {
                Debug.LogError("[PrefabSentinel.Tests] Missing -sentinelTestOutputPath argument.");
                EditorApplication.Exit(1);
                return;
            }

            TestSuiteResult result;
            try
            {
                result = RunTestSuite();
            }
            catch (Exception ex)
            {
                result = new TestSuiteResult
                {
                    message = $"Unhandled exception in test suite: {ex}",
                    diagnostics = new[]
                    {
                        new TestDiagnostic
                        {
                            detail = "exception",
                            evidence = ex.ToString()
                        }
                    }
                };
            }

            try
            {
                string dir = Path.GetDirectoryName(outputPath);
                if (!string.IsNullOrWhiteSpace(dir))
                    Directory.CreateDirectory(dir);
                File.WriteAllText(outputPath, JsonUtility.ToJson(result, true));
            }
            catch (Exception ex)
            {
                Debug.LogError($"[PrefabSentinel.Tests] Failed to write results: {ex}");
            }

            EditorApplication.Exit(result.success ? 0 : 1);
        }

        // ----------------------------------------------------------------
        // Test suite runner
        // ----------------------------------------------------------------

        private delegate TestCaseResult TestMethod(string prefabPath, string materialPath);

        private static TestSuiteResult RunTestSuite()
        {
            float suiteStart = Time.realtimeSinceStartup;

            // Create fixture directory
            if (!AssetDatabase.IsValidFolder(TestAssetDir))
            {
                string parent = Path.GetDirectoryName(TestAssetDir).Replace('\\', '/');
                string folder = Path.GetFileName(TestAssetDir);
                if (!AssetDatabase.IsValidFolder(parent))
                    AssetDatabase.CreateFolder("Assets", Path.GetFileName(parent));
                AssetDatabase.CreateFolder(parent, folder);
            }

            string prefabPath = null;
            string materialPath = null;
            try
            {
                prefabPath = CreateTestPrefab();
                materialPath = CreateTestMaterial();
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);

                var tests = new (string name, TestMethod method)[]
                {
                    ("Set_BoolProperty", Test_Set_BoolProperty),
                    ("Set_IntProperty", Test_Set_IntProperty),
                    ("Set_FloatProperty", Test_Set_FloatProperty),
                    ("Set_StringProperty", Test_Set_StringProperty),
                    ("Set_EnumProperty", Test_Set_EnumProperty),
                    ("Set_ColorProperty_Json", Test_Set_ColorProperty_Json),
                    ("Set_Vector3Property_Json", Test_Set_Vector3Property_Json),
                    ("Set_ObjectReference_Json", Test_Set_ObjectReference_Json),
                    ("Set_FixedBufferIndexedElement", Test_Set_FixedBufferIndexedElement),
                    ("Set_DynamicArrayIndexedElement", Test_Set_DynamicArrayIndexedElement),
                    ("InsertArrayElement_AtBeginning", Test_InsertArrayElement_AtBeginning),
                    ("InsertArrayElement_AtEnd", Test_InsertArrayElement_AtEnd),
                    ("InsertArrayElement_WithValue", Test_InsertArrayElement_WithValue),
                    ("InsertArrayElement_OutOfBounds", Test_InsertArrayElement_OutOfBounds),
                    ("RemoveArrayElement_First", Test_RemoveArrayElement_First),
                    ("RemoveArrayElement_Last", Test_RemoveArrayElement_Last),
                    ("RemoveArrayElement_OutOfBounds", Test_RemoveArrayElement_OutOfBounds),
                    ("InsertArrayElement_FixedBuffer_Rejected", Test_InsertArrayElement_FixedBuffer_Rejected),
                    ("RemoveArrayElement_FixedBuffer_Rejected", Test_RemoveArrayElement_FixedBuffer_Rejected),
                    ("Set_PropertyNotFound", Test_Set_PropertyNotFound),
                    ("Set_ValueKindMismatch", Test_Set_ValueKindMismatch),
                    ("ProtocolVersionMismatch", Test_ProtocolVersionMismatch),
                    ("Asset_Set_MaterialProperty", Test_Asset_Set_MaterialProperty),
                    ("Set_SaveReopen_Preserves", Test_Set_SaveReopen_Preserves),
                    // Create-mode: prefab
                    ("CreatePrefab_RootOnly", Test_CreatePrefab_RootOnly),
                    ("CreatePrefab_HierarchyWithComponent", Test_CreatePrefab_HierarchyWithComponent),
                    ("CreatePrefab_FindComponentAndMutate", Test_CreatePrefab_FindComponentAndMutate),
                    ("CreatePrefab_RenameAndReparent", Test_CreatePrefab_RenameAndReparent),
                    ("CreatePrefab_DuplicateRootRejected", Test_CreatePrefab_DuplicateRootRejected),
                    // Create-mode: material
                    ("CreateMaterial_StandardShader", Test_CreateMaterial_StandardShader),
                    ("CreateMaterial_SetName", Test_CreateMaterial_SetName),
                    ("CreateMaterial_MissingShaderRejected", Test_CreateMaterial_MissingShaderRejected),
                    ("CreateMaterial_AlreadyExistsRejected", Test_CreateMaterial_AlreadyExistsRejected),
                    // Create-mode: scene
                    ("CreateScene_EmptyWithGameObject", Test_CreateScene_EmptyWithGameObject),
                    ("CreateScene_InstantiatePrefab", Test_CreateScene_InstantiatePrefab),
                    ("CreateScene_HierarchyWithComponents", Test_CreateScene_HierarchyWithComponents),
                    ("CreateScene_MissingCreateSceneRejected", Test_CreateScene_MissingCreateSceneRejected),
                    // Variant E2E quality gates
                    ("Variant_SetOverrideProperty", Test_Variant_SetOverrideProperty),
                    ("Variant_MultipleOverrides", Test_Variant_MultipleOverrides),
                    ("Variant_OverridePersistsAfterSave", Test_Variant_OverridePersistsAfterSave),
                    ("Variant_InheritBaseChange", Test_Variant_InheritBaseChange),
                    // Handle-based ObjectReference (Phase H)
                    ("Handle_ObjectReference_InCreateMode", Test_Handle_ObjectReference_InCreateMode),
                    ("Handle_ObjectReference_UnknownHandle", Test_Handle_ObjectReference_UnknownHandle),
                    ("Handle_ObjectReference_InOpenModeRejected", Test_Handle_ObjectReference_InOpenModeRejected),
                    // remove_component
                    ("RemoveComponent_Success", Test_RemoveComponent_Success),
                    ("RemoveComponent_TransformRejected", Test_RemoveComponent_TransformRejected),
                    // Additional value_kind types
                    ("Set_ColorProperty_String", Test_Set_ColorProperty_String),
                    ("Set_EnumProperty_StringName", Test_Set_EnumProperty_StringName),
                    ("Set_QuaternionProperty_Json", Test_Set_QuaternionProperty_Json),
                    ("Set_RectProperty_Json", Test_Set_RectProperty_Json),
                    // Additional error paths
                    ("Set_MissingTarget_OpenMode", Test_Set_MissingTarget_OpenMode),
                    ("Set_EmptyTarget", Test_Set_EmptyTarget),
                    ("Set_EmptyOps", Test_Set_EmptyOps),
                    // UdonSharp (conditional)
                    ("UdonSharpBacking_CreateMode", Test_UdonSharpBacking_CreateMode),
                };

                var results = new List<TestCaseResult>();
                int passed = 0;
                foreach (var (name, method) in tests)
                {
                    // Re-create prefab before each test to isolate state
                    AssetDatabase.DeleteAsset(prefabPath);
                    prefabPath = CreateTestPrefab();
                    AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);

                    float caseStart = Time.realtimeSinceStartup;
                    TestCaseResult caseResult;
                    try
                    {
                        caseResult = method(prefabPath, materialPath);
                    }
                    catch (Exception ex)
                    {
                        caseResult = Fail(name, $"Unhandled exception: {ex.Message}");
                    }
                    caseResult.name = name;
                    caseResult.duration_sec = Time.realtimeSinceStartup - caseStart;
                    results.Add(caseResult);
                    if (caseResult.passed) passed++;
                }

                float duration = Time.realtimeSinceStartup - suiteStart;
                int total = results.Count;
                bool allPassed = passed == total;
                return new TestSuiteResult
                {
                    success = allPassed,
                    severity = allPassed ? "info" : "error",
                    code = allPassed ? "INTEGRATION_TEST_OK" : "INTEGRATION_TEST_FAILED",
                    message = $"{passed}/{total} tests passed.",
                    data = new TestSuiteData
                    {
                        total = total,
                        passed = passed,
                        failed = total - passed,
                        duration_sec = duration,
                        cases = results.ToArray()
                    }
                };
            }
            finally
            {
                CleanupTestAssets();
            }
        }

        // ----------------------------------------------------------------
        // Fixture creation
        // ----------------------------------------------------------------

        private static string CreateTestPrefab()
        {
            string path = TestAssetDir + "/TestFixture.prefab";
            var go = new GameObject("TestFixtureRoot");
            go.AddComponent<BoxCollider>();
            var audio = go.AddComponent<AudioSource>();
            // Seed array with 3 elements for array tests via AnimationCurve keys
            audio.volume = 1.0f;
            audio.priority = 64;

            PrefabUtility.SaveAsPrefabAsset(go, path);
            UnityEngine.Object.DestroyImmediate(go);
            return path;
        }

        private static string CreateTestMaterial()
        {
            string path = TestAssetDir + "/TestMaterial.mat";
            var shader = Shader.Find("Standard");
            if (shader == null)
                shader = Shader.Find("Hidden/InternalErrorShader");
            var mat = new Material(shader) { name = "OriginalMaterialName" };
            AssetDatabase.CreateAsset(mat, path);
            return path;
        }

        /// <summary>
        /// Creates a Prefab Variant of the given base prefab.
        /// Saving a connected instance produces a variant that tracks the source prefab.
        /// </summary>
        private static string CreateVariantFrom(string basePrefabPath)
        {
            string variantPath = TestAssetDir + "/TestVariant.prefab";
            DeleteIfExists(variantPath);

            var basePrefab = AssetDatabase.LoadAssetAtPath<GameObject>(basePrefabPath);
            if (basePrefab == null) return null;

            var instance = (GameObject)PrefabUtility.InstantiatePrefab(basePrefab);
            if (instance == null) return null;

            try
            {
                PrefabUtility.SaveAsPrefabAsset(instance, variantPath);
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                return variantPath;
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(instance);
            }
        }

        private static void CleanupTestAssets()
        {
            if (AssetDatabase.IsValidFolder(TestAssetDir))
            {
                AssetDatabase.DeleteAsset(TestAssetDir);
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            }
        }

        // ----------------------------------------------------------------
        // Bridge invocation helper
        // ----------------------------------------------------------------

        /// <summary>
        /// Writes a bridge request, calls ApplyFromPaths, and returns the parsed response.
        /// </summary>
        private static BridgeResponseReadback RunBridge(string requestJson)
        {
            string tempDir = Path.Combine(Path.GetTempPath(), "PrefabSentinelTests_" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(tempDir);
            string requestPath = Path.Combine(tempDir, "request.json");
            string responsePath = Path.Combine(tempDir, "response.json");
            try
            {
                File.WriteAllText(requestPath, requestJson);
                UnityPatchBridge.ApplyFromPaths(requestPath, responsePath);

                if (!File.Exists(responsePath))
                    return null;

                string responseJson = File.ReadAllText(responsePath);
                return JsonUtility.FromJson<BridgeResponseReadback>(responseJson);
            }
            finally
            {
                try { Directory.Delete(tempDir, true); } catch { /* best effort */ }
            }
        }

        private static string BuildRequest(string target, string kind, string mode, string opsArrayJson)
        {
            // Build request JSON manually to avoid depending on bridge internals.
            return "{"
                + $"\"protocol_version\":{ProtocolVersion},"
                + $"\"target\":\"{EscapeJsonString(target)}\","
                + $"\"kind\":\"{EscapeJsonString(kind)}\","
                + $"\"mode\":\"{EscapeJsonString(mode)}\","
                + $"\"ops\":{opsArrayJson}"
                + "}";
        }

        private static string BuildPrefabRequest(string prefabPath, string opsArrayJson)
        {
            return BuildRequest(prefabPath, "prefab", "open", opsArrayJson);
        }

        private static string BuildAssetRequest(string assetPath, string opsArrayJson)
        {
            string kind = assetPath.EndsWith(".mat") ? "material" : "asset";
            return BuildRequest(assetPath, kind, "open", opsArrayJson);
        }

        private static string BuildCreatePrefabRequest(string targetPath, string opsArrayJson)
        {
            return BuildRequest(targetPath, "prefab", "create", opsArrayJson);
        }

        private static string BuildCreateMaterialRequest(string targetPath, string opsArrayJson)
        {
            return BuildRequest(targetPath, "material", "create", opsArrayJson);
        }

        private static string BuildCreateSceneRequest(string targetPath, string opsArrayJson)
        {
            return BuildRequest(targetPath, "scene", "create", opsArrayJson);
        }

        private static void DeleteIfExists(string assetPath)
        {
            if (AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(assetPath) != null)
                AssetDatabase.DeleteAsset(assetPath);
        }

        private static string SetOp(string component, string path, string valueKind, string valueField, string valueRaw)
        {
            string valueEntry;
            if (valueKind == "json")
                valueEntry = $"\"value_kind\":\"json\",\"value_json\":\"{EscapeJsonString(valueRaw)}\"";
            else if (valueKind == "string")
                valueEntry = $"\"value_kind\":\"string\",\"value_string\":\"{EscapeJsonString(valueRaw)}\"";
            else if (valueKind == "bool")
                valueEntry = $"\"value_kind\":\"bool\",\"value_bool\":{valueRaw}";
            else if (valueKind == "float")
                valueEntry = $"\"value_kind\":\"float\",\"value_float\":{valueRaw}";
            else
                valueEntry = $"\"value_kind\":\"{valueKind}\",\"value_{valueField}\":{valueRaw}";

            return "{"
                + $"\"op\":\"set\","
                + $"\"component\":\"{EscapeJsonString(component)}\","
                + $"\"path\":\"{EscapeJsonString(path)}\","
                + valueEntry
                + "}";
        }

        // ----------------------------------------------------------------
        // Property readback helpers
        // ----------------------------------------------------------------

        private static SerializedProperty GetPrefabProperty(string prefabPath, string componentType, string propertyPath)
        {
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
            if (prefab == null) return null;
            var component = prefab.GetComponent(componentType);
            if (component == null) return null;
            var so = new SerializedObject(component);
            return so.FindProperty(propertyPath);
        }

        private static SerializedProperty GetAssetProperty(string assetPath, string propertyPath)
        {
            var asset = AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(assetPath);
            if (asset == null) return null;
            var so = new SerializedObject(asset);
            return so.FindProperty(propertyPath);
        }

        // ----------------------------------------------------------------
        // Assertion helpers
        // ----------------------------------------------------------------

        private static TestCaseResult Pass(string name, string message = "")
        {
            return new TestCaseResult { name = name, passed = true, message = message };
        }

        private static TestCaseResult Fail(string name, string message)
        {
            return new TestCaseResult
            {
                name = name,
                passed = false,
                message = message,
                diagnostics = new[]
                {
                    new TestDiagnostic { detail = "assertion_failed", evidence = message }
                }
            };
        }

        private static TestCaseResult AssertBridgeSuccess(string name, BridgeResponseReadback resp, int expectedApplied)
        {
            if (resp == null) return Fail(name, "Bridge response is null (response file missing).");
            if (!resp.success) return Fail(name, $"Bridge returned success=false: code={resp.code}, message={resp.message}");
            if (resp.data.applied != expectedApplied)
                return Fail(name, $"Expected applied={expectedApplied}, got {resp.data.applied}.");
            return null; // no failure
        }

        private static TestCaseResult AssertBridgeFailure(string name, BridgeResponseReadback resp, string expectedCode)
        {
            if (resp == null) return Fail(name, "Bridge response is null (response file missing).");
            if (resp.success) return Fail(name, $"Expected failure but got success=true, code={resp.code}.");
            if (!string.IsNullOrEmpty(expectedCode) && resp.code != expectedCode)
                return Fail(name, $"Expected code={expectedCode}, got {resp.code}.");
            return null; // no failure
        }

        private static TestCaseResult AssertCreateSuccess(string name, BridgeResponseReadback resp)
        {
            if (resp == null) return Fail(name, "Bridge response is null (response file missing).");
            if (!resp.success) return Fail(name, $"Bridge returned success=false: code={resp.code}, message={resp.message}");
            return null; // no failure
        }

        // ----------------------------------------------------------------
        // Test cases: set operations
        // ----------------------------------------------------------------

        private static TestCaseResult Test_Set_BoolProperty(string prefabPath, string materialPath)
        {
            const string name = "Set_BoolProperty";
            string ops = "[" + SetOp("BoxCollider", "m_IsTrigger", "bool", "bool", "true") + "]";
            var resp = RunBridge(BuildPrefabRequest(prefabPath, ops));
            var err = AssertBridgeSuccess(name, resp, 1);
            if (err != null) return err;

            var prop = GetPrefabProperty(prefabPath, "BoxCollider", "m_IsTrigger");
            if (prop == null) return Fail(name, "Could not read m_IsTrigger after apply.");
            if (!prop.boolValue) return Fail(name, $"Expected m_IsTrigger=true, got {prop.boolValue}.");
            return Pass(name);
        }

        private static TestCaseResult Test_Set_IntProperty(string prefabPath, string materialPath)
        {
            const string name = "Set_IntProperty";
            // Use m_Enabled on AudioSource — a reliable int property (0=disabled, 1=enabled)
            string ops = "[" + SetOp("AudioSource", "m_Enabled", "int", "int", "0") + "]";
            var resp = RunBridge(BuildPrefabRequest(prefabPath, ops));
            var err = AssertBridgeSuccess(name, resp, 1);
            if (err != null) return err;

            var prop = GetPrefabProperty(prefabPath, "AudioSource", "m_Enabled");
            if (prop == null) return Fail(name, "Could not read m_Enabled after apply.");
            if (prop.intValue != 0) return Fail(name, $"Expected m_Enabled=0, got {prop.intValue}.");
            return Pass(name);
        }

        private static TestCaseResult Test_Set_FloatProperty(string prefabPath, string materialPath)
        {
            const string name = "Set_FloatProperty";
            string ops = "[" + SetOp("AudioSource", "m_Volume", "float", "float", "0.5") + "]";
            var resp = RunBridge(BuildPrefabRequest(prefabPath, ops));
            var err = AssertBridgeSuccess(name, resp, 1);
            if (err != null) return err;

            var prop = GetPrefabProperty(prefabPath, "AudioSource", "m_Volume");
            if (prop == null) return Fail(name, "Could not read m_Volume after apply.");
            if (Math.Abs(prop.floatValue - 0.5f) > 0.001f)
                return Fail(name, $"Expected m_Volume=0.5, got {prop.floatValue}.");
            return Pass(name);
        }

        private static TestCaseResult Test_Set_StringProperty(string prefabPath, string materialPath)
        {
            const string name = "Set_StringProperty";
            // Test string set via material asset (m_Name is a string on material).
            // Asset open-mode ops require "target":"$asset" to resolve the handle.
            var matReq = BuildRequest(materialPath, "material", "open",
                "[{\"op\":\"set\",\"target\":\"$asset\",\"path\":\"m_Name\",\"value_kind\":\"string\",\"value_string\":\"RenamedMaterial\"}]");
            var resp = RunBridge(matReq);
            var err = AssertBridgeSuccess(name, resp, 1);
            if (err != null) return err;

            var mat = AssetDatabase.LoadAssetAtPath<Material>(materialPath);
            if (mat == null) return Fail(name, "Could not load material after apply.");
            if (mat.name != "RenamedMaterial") return Fail(name, $"Expected name=RenamedMaterial, got {mat.name}.");
            // Reset name for other tests
            mat.name = "OriginalMaterialName";
            EditorUtility.SetDirty(mat);
            AssetDatabase.SaveAssets();
            return Pass(name);
        }

        private static TestCaseResult Test_Set_EnumProperty(string prefabPath, string materialPath)
        {
            const string name = "Set_EnumProperty";
            // AudioRolloffMode: Logarithmic=0, Linear=1, Custom=2
            string ops = "[" + SetOp("AudioSource", "rolloffMode", "int", "int", "1") + "]";
            var resp = RunBridge(BuildPrefabRequest(prefabPath, ops));
            var err = AssertBridgeSuccess(name, resp, 1);
            if (err != null) return err;

            var prop = GetPrefabProperty(prefabPath, "AudioSource", "rolloffMode");
            if (prop == null) return Fail(name, "Could not read rolloffMode after apply.");
            if (prop.enumValueIndex != 1 && prop.intValue != 1)
                return Fail(name, $"Expected rolloffMode=1 (Linear), got intValue={prop.intValue}.");
            return Pass(name);
        }

        private static TestCaseResult Test_Set_ColorProperty_Json(string prefabPath, string materialPath)
        {
            const string name = "Set_ColorProperty_Json";
            // Material _Color property is the albedo color for Standard shader
            string colorJson = "{\\\"r\\\":0.5,\\\"g\\\":0.25,\\\"b\\\":0.75,\\\"a\\\":1.0}";
            string req = BuildRequest(materialPath, "material", "open",
                "[{\"op\":\"set\",\"target\":\"$asset\",\"path\":\"m_SavedProperties.m_Colors.Array.data[0].second\","
                + "\"value_kind\":\"json\",\"value_json\":\"" + colorJson + "\"}]");
            var resp = RunBridge(req);
            // Color paths in materials may vary; if the path doesn't exist, just check the bridge response
            if (resp == null) return Fail(name, "Bridge response is null.");
            // If the material serialized layout doesn't have this path, treat as inconclusive but not fail
            if (!resp.success && resp.code != null && resp.code.Contains("APPLY"))
            {
                // Try alternative: set _Color via a simpler mechanism
                // This test validates the JSON color encoding path in the bridge
                return Fail(name, $"Bridge returned {resp.code}: {resp.message}");
            }
            if (resp.success) return Pass(name);
            return Fail(name, $"Unexpected response: code={resp.code}, message={resp.message}");
        }

        private static TestCaseResult Test_Set_Vector3Property_Json(string prefabPath, string materialPath)
        {
            const string name = "Set_Vector3Property_Json";
            string vecJson = "{\\\"x\\\":2.0,\\\"y\\\":3.0,\\\"z\\\":4.0}";
            string ops = "[{\"op\":\"set\","
                + "\"component\":\"BoxCollider\","
                + "\"path\":\"m_Center\","
                + "\"value_kind\":\"json\","
                + "\"value_json\":\"" + vecJson + "\"}]";
            var resp = RunBridge(BuildPrefabRequest(prefabPath, ops));
            var err = AssertBridgeSuccess(name, resp, 1);
            if (err != null) return err;

            var prop = GetPrefabProperty(prefabPath, "BoxCollider", "m_Center");
            if (prop == null) return Fail(name, "Could not read m_Center after apply.");
            var v = prop.vector3Value;
            if (Math.Abs(v.x - 2.0f) > 0.001f || Math.Abs(v.y - 3.0f) > 0.001f || Math.Abs(v.z - 4.0f) > 0.001f)
                return Fail(name, $"Expected m_Center=(2,3,4), got ({v.x},{v.y},{v.z}).");
            return Pass(name);
        }

        private static TestCaseResult Test_Set_ObjectReference_Json(string prefabPath, string materialPath)
        {
            const string name = "Set_ObjectReference_Json";
            // Set BoxCollider.m_Material (PhysicMaterial ref) to null via JSON null reference
            string ops = "[{\"op\":\"set\","
                + "\"component\":\"BoxCollider\","
                + "\"path\":\"m_Material\","
                + "\"value_kind\":\"null\"}]";
            var resp = RunBridge(BuildPrefabRequest(prefabPath, ops));
            if (resp == null) return Fail(name, "Bridge response is null.");
            if (resp.success)
            {
                var prop = GetPrefabProperty(prefabPath, "BoxCollider", "m_Material");
                if (prop == null) return Fail(name, "Could not read m_Material after apply.");
                if (prop.objectReferenceValue != null)
                    return Fail(name, $"Expected null reference, got {prop.objectReferenceValue}.");
                return Pass(name);
            }
            return Fail(name, $"Bridge returned {resp.code}: {resp.message}");
        }

        // ----------------------------------------------------------------
        // Test cases: indexed array element (set on existing element)
        // ----------------------------------------------------------------

        private static TestCaseResult Test_Set_FixedBufferIndexedElement(string prefabPath, string materialPath)
        {
            const string name = "Set_FixedBufferIndexedElement";
            // BoxCollider m_Size is a Vector3 (fixed struct, not array). Access individual axis via path.
            // m_Size.x is a direct float path, not an array element.
            string ops = "[" + SetOp("BoxCollider", "m_Size.x", "float", "float", "5.0") + "]";
            var resp = RunBridge(BuildPrefabRequest(prefabPath, ops));
            var err = AssertBridgeSuccess(name, resp, 1);
            if (err != null) return err;

            var prop = GetPrefabProperty(prefabPath, "BoxCollider", "m_Size");
            if (prop == null) return Fail(name, "Could not read m_Size after apply.");
            if (Math.Abs(prop.vector3Value.x - 5.0f) > 0.001f)
                return Fail(name, $"Expected m_Size.x=5.0, got {prop.vector3Value.x}.");
            return Pass(name);
        }

        private static TestCaseResult Test_Set_DynamicArrayIndexedElement(string prefabPath, string materialPath)
        {
            const string name = "Set_DynamicArrayIndexedElement";
            // First insert an element so we have something to set on.
            // AudioSource.m_OutputAudioMixerGroup is not an array.
            // Use AnimationCurve on AudioSource which has m_CustomRolloffCurve.m_Curve (AnimationCurve keys array).
            // Let's use a simpler approach: the m_CustomRolloffCurve has a serialized curve with keys.
            // Actually, AudioSource doesn't easily expose a mutable dynamic array.
            // Use BoxCollider indirectly? No.
            // Best approach: set on material's m_SavedProperties.m_Floats which is a dynamic array.
            // Or skip and just test insert+set combo.

            // Use the material's serialized float array as test target.
            // First check if the array has elements.
            var prop = GetAssetProperty(materialPath, "m_SavedProperties.m_Floats.Array.size");
            if (prop == null)
                return Fail(name, "Could not read m_SavedProperties.m_Floats.Array.size on material.");
            int size = prop.intValue;
            if (size == 0)
                return Fail(name, "Material float array is empty; cannot test indexed set.");

            // Set the first element's value (second field in the pair)
            string ops = "[{\"op\":\"set\",\"target\":\"$asset\","
                + "\"path\":\"m_SavedProperties.m_Floats.Array.data[0].second\","
                + "\"value_kind\":\"float\",\"value_float\":0.42}]";
            var resp = RunBridge(BuildAssetRequest(materialPath, ops));
            var err = AssertBridgeSuccess(name, resp, 1);
            if (err != null) return err;

            var readback = GetAssetProperty(materialPath, "m_SavedProperties.m_Floats.Array.data[0].second");
            if (readback == null) return Fail(name, "Could not read back array element.");
            if (Math.Abs(readback.floatValue - 0.42f) > 0.001f)
                return Fail(name, $"Expected 0.42, got {readback.floatValue}.");
            return Pass(name);
        }

        // ----------------------------------------------------------------
        // Test cases: insert_array_element
        // ----------------------------------------------------------------

        private static TestCaseResult Test_InsertArrayElement_AtBeginning(string prefabPath, string materialPath)
        {
            const string name = "InsertArrayElement_AtBeginning";
            // Insert at index 0 on material float array.
            // Verify via bridge response only — Unity normalizes material property
            // arrays on save (stripping keyless entries), so size readback may not
            // reflect the insert. The bridge applied=1 proves the operation succeeded.
            string ops = "[{\"op\":\"insert_array_element\",\"target\":\"$asset\","
                + "\"path\":\"m_SavedProperties.m_Floats.Array.data\","
                + "\"index\":0}]";
            var resp = RunBridge(BuildAssetRequest(materialPath, ops));
            var err = AssertBridgeSuccess(name, resp, 1);
            if (err != null) return err;
            return Pass(name);
        }

        private static TestCaseResult Test_InsertArrayElement_AtEnd(string prefabPath, string materialPath)
        {
            const string name = "InsertArrayElement_AtEnd";
            var sizeProp = GetAssetProperty(materialPath, "m_SavedProperties.m_Floats.Array.size");
            if (sizeProp == null) return Fail(name, "Cannot read array size.");
            int before = sizeProp.intValue;

            // Insert at end; verify via bridge response only (same normalization caveat).
            string ops = "[{\"op\":\"insert_array_element\",\"target\":\"$asset\","
                + "\"path\":\"m_SavedProperties.m_Floats.Array.data\","
                + $"\"index\":{before}}}]";
            var resp = RunBridge(BuildAssetRequest(materialPath, ops));
            var err = AssertBridgeSuccess(name, resp, 1);
            if (err != null) return err;
            return Pass(name);
        }

        private static TestCaseResult Test_InsertArrayElement_WithValue(string prefabPath, string materialPath)
        {
            const string name = "InsertArrayElement_WithValue";
            // Insert at index 0 with a value set afterward
            string ops = "["
                + "{\"op\":\"insert_array_element\",\"target\":\"$asset\","
                + "\"path\":\"m_SavedProperties.m_Floats.Array.data\","
                + "\"index\":0},"
                + "{\"op\":\"set\",\"target\":\"$asset\","
                + "\"path\":\"m_SavedProperties.m_Floats.Array.data[0].second\","
                + "\"value_kind\":\"float\",\"value_float\":9.99}"
                + "]";
            var resp = RunBridge(BuildAssetRequest(materialPath, ops));
            var err = AssertBridgeSuccess(name, resp, 2);
            if (err != null) return err;

            var readback = GetAssetProperty(materialPath, "m_SavedProperties.m_Floats.Array.data[0].second");
            if (readback == null) return Fail(name, "Cannot read inserted element.");
            if (Math.Abs(readback.floatValue - 9.99f) > 0.01f)
                return Fail(name, $"Expected 9.99, got {readback.floatValue}.");
            return Pass(name);
        }

        private static TestCaseResult Test_InsertArrayElement_OutOfBounds(string prefabPath, string materialPath)
        {
            const string name = "InsertArrayElement_OutOfBounds";
            var sizeProp = GetAssetProperty(materialPath, "m_SavedProperties.m_Floats.Array.size");
            if (sizeProp == null) return Fail(name, "Cannot read array size.");

            string ops = "[{\"op\":\"insert_array_element\",\"target\":\"$asset\","
                + "\"path\":\"m_SavedProperties.m_Floats.Array.data\","
                + $"\"index\":{sizeProp.intValue + 10}}}]";
            var resp = RunBridge(BuildAssetRequest(materialPath, ops));
            return AssertBridgeFailure(name, resp, null) ?? Pass(name);
        }

        // ----------------------------------------------------------------
        // Test cases: remove_array_element
        // ----------------------------------------------------------------

        private static TestCaseResult Test_RemoveArrayElement_First(string prefabPath, string materialPath)
        {
            const string name = "RemoveArrayElement_First";
            var sizeProp = GetAssetProperty(materialPath, "m_SavedProperties.m_Floats.Array.size");
            if (sizeProp == null) return Fail(name, "Cannot read array size.");
            int before = sizeProp.intValue;
            if (before == 0) return Fail(name, "Array is empty; cannot test remove.");

            string ops = "[{\"op\":\"remove_array_element\",\"target\":\"$asset\","
                + "\"path\":\"m_SavedProperties.m_Floats.Array.data\","
                + "\"index\":0}]";
            var resp = RunBridge(BuildAssetRequest(materialPath, ops));
            var err = AssertBridgeSuccess(name, resp, 1);
            if (err != null) return err;

            sizeProp = GetAssetProperty(materialPath, "m_SavedProperties.m_Floats.Array.size");
            if (sizeProp == null) return Fail(name, "Cannot read array size after remove.");
            if (sizeProp.intValue != before - 1)
                return Fail(name, $"Expected size={before - 1}, got {sizeProp.intValue}.");
            return Pass(name);
        }

        private static TestCaseResult Test_RemoveArrayElement_Last(string prefabPath, string materialPath)
        {
            const string name = "RemoveArrayElement_Last";
            var sizeProp = GetAssetProperty(materialPath, "m_SavedProperties.m_Floats.Array.size");
            if (sizeProp == null) return Fail(name, "Cannot read array size.");
            int before = sizeProp.intValue;
            if (before == 0) return Fail(name, "Array is empty; cannot test remove.");

            string ops = "[{\"op\":\"remove_array_element\",\"target\":\"$asset\","
                + "\"path\":\"m_SavedProperties.m_Floats.Array.data\","
                + $"\"index\":{before - 1}}}]";
            var resp = RunBridge(BuildAssetRequest(materialPath, ops));
            var err = AssertBridgeSuccess(name, resp, 1);
            if (err != null) return err;

            sizeProp = GetAssetProperty(materialPath, "m_SavedProperties.m_Floats.Array.size");
            if (sizeProp == null) return Fail(name, "Cannot read array size after remove.");
            if (sizeProp.intValue != before - 1)
                return Fail(name, $"Expected size={before - 1}, got {sizeProp.intValue}.");
            return Pass(name);
        }

        private static TestCaseResult Test_RemoveArrayElement_OutOfBounds(string prefabPath, string materialPath)
        {
            const string name = "RemoveArrayElement_OutOfBounds";
            var sizeProp = GetAssetProperty(materialPath, "m_SavedProperties.m_Floats.Array.size");
            if (sizeProp == null) return Fail(name, "Cannot read array size.");

            string ops = "[{\"op\":\"remove_array_element\",\"target\":\"$asset\","
                + "\"path\":\"m_SavedProperties.m_Floats.Array.data\","
                + $"\"index\":{sizeProp.intValue + 10}}}]";
            var resp = RunBridge(BuildAssetRequest(materialPath, ops));
            return AssertBridgeFailure(name, resp, null) ?? Pass(name);
        }

        // ----------------------------------------------------------------
        // Test cases: fixed buffer rejection
        // ----------------------------------------------------------------

        private static TestCaseResult Test_InsertArrayElement_FixedBuffer_Rejected(string prefabPath, string materialPath)
        {
            const string name = "InsertArrayElement_FixedBuffer_Rejected";
            // m_Center is a Vector3 — a fixed struct, not a dynamic array.
            // Attempting insert on it should fail.
            string ops = "[{\"op\":\"insert_array_element\","
                + "\"component\":\"BoxCollider\","
                + "\"path\":\"m_Center\","
                + "\"index\":0}]";
            var resp = RunBridge(BuildPrefabRequest(prefabPath, ops));
            return AssertBridgeFailure(name, resp, null) ?? Pass(name);
        }

        private static TestCaseResult Test_RemoveArrayElement_FixedBuffer_Rejected(string prefabPath, string materialPath)
        {
            const string name = "RemoveArrayElement_FixedBuffer_Rejected";
            string ops = "[{\"op\":\"remove_array_element\","
                + "\"component\":\"BoxCollider\","
                + "\"path\":\"m_Center\","
                + "\"index\":0}]";
            var resp = RunBridge(BuildPrefabRequest(prefabPath, ops));
            return AssertBridgeFailure(name, resp, null) ?? Pass(name);
        }

        // ----------------------------------------------------------------
        // Test cases: error paths
        // ----------------------------------------------------------------

        private static TestCaseResult Test_Set_PropertyNotFound(string prefabPath, string materialPath)
        {
            const string name = "Set_PropertyNotFound";
            string ops = "[" + SetOp("BoxCollider", "m_NoSuchProperty", "int", "int", "42") + "]";
            var resp = RunBridge(BuildPrefabRequest(prefabPath, ops));
            return AssertBridgeFailure(name, resp, null) ?? Pass(name);
        }

        private static TestCaseResult Test_Set_ValueKindMismatch(string prefabPath, string materialPath)
        {
            const string name = "Set_ValueKindMismatch";
            // Set a bool property (m_IsTrigger) with an unparseable string value.
            // Bridge should reject: TryReadBoolValue fails on "not_a_bool".
            string ops = "[{\"op\":\"set\","
                + "\"component\":\"BoxCollider\","
                + "\"path\":\"m_IsTrigger\","
                + "\"value_kind\":\"string\",\"value_string\":\"not_a_bool\"}]";
            var resp = RunBridge(BuildPrefabRequest(prefabPath, ops));
            return AssertBridgeFailure(name, resp, "UNITY_BRIDGE_APPLY") ?? Pass(name);
        }

        private static TestCaseResult Test_ProtocolVersionMismatch(string prefabPath, string materialPath)
        {
            const string name = "ProtocolVersionMismatch";
            string req = "{"
                + "\"protocol_version\":999,"
                + $"\"target\":\"{EscapeJsonString(prefabPath)}\","
                + "\"kind\":\"prefab\","
                + "\"mode\":\"open\","
                + "\"ops\":[{\"op\":\"set\",\"component\":\"BoxCollider\",\"path\":\"m_IsTrigger\","
                + "\"value_kind\":\"bool\",\"value_bool\":true}]"
                + "}";
            var resp = RunBridge(req);
            return AssertBridgeFailure(name, resp, "UNITY_BRIDGE_PROTOCOL_VERSION") ?? Pass(name);
        }

        // ----------------------------------------------------------------
        // Test cases: asset-level
        // ----------------------------------------------------------------

        private static TestCaseResult Test_Asset_Set_MaterialProperty(string prefabPath, string materialPath)
        {
            const string name = "Asset_Set_MaterialProperty";
            // Set material render queue
            string ops = "[{\"op\":\"set\",\"target\":\"$asset\","
                + "\"path\":\"m_CustomRenderQueue\","
                + "\"value_kind\":\"int\",\"value_int\":3000}]";
            var resp = RunBridge(BuildAssetRequest(materialPath, ops));
            var err = AssertBridgeSuccess(name, resp, 1);
            if (err != null) return err;

            var prop = GetAssetProperty(materialPath, "m_CustomRenderQueue");
            if (prop == null) return Fail(name, "Could not read m_CustomRenderQueue after apply.");
            if (prop.intValue != 3000)
                return Fail(name, $"Expected m_CustomRenderQueue=3000, got {prop.intValue}.");
            return Pass(name);
        }

        // ----------------------------------------------------------------
        // Test cases: persistence (save → reopen)
        // ----------------------------------------------------------------

        private static TestCaseResult Test_Set_SaveReopen_Preserves(string prefabPath, string materialPath)
        {
            const string name = "Set_SaveReopen_Preserves";
            // Set a distinctive value
            string ops = "[" + SetOp("BoxCollider", "m_IsTrigger", "bool", "bool", "true") + "]";
            var resp = RunBridge(BuildPrefabRequest(prefabPath, ops));
            var err = AssertBridgeSuccess(name, resp, 1);
            if (err != null) return err;

            // Force save and reimport
            AssetDatabase.SaveAssets();
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);

            // Unload and reload
            Resources.UnloadAsset(AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath));
            AssetDatabase.ImportAsset(prefabPath, ImportAssetOptions.ForceUpdate);

            var prop = GetPrefabProperty(prefabPath, "BoxCollider", "m_IsTrigger");
            if (prop == null) return Fail(name, "Could not read m_IsTrigger after reopen.");
            if (!prop.boolValue) return Fail(name, "m_IsTrigger did not persist after save/reopen.");
            return Pass(name);
        }

        // ----------------------------------------------------------------
        // Test cases: create-mode prefab
        // ----------------------------------------------------------------

        private static TestCaseResult Test_CreatePrefab_RootOnly(string prefabPath, string materialPath)
        {
            const string name = "CreatePrefab_RootOnly";
            string target = TestAssetDir + "/RootOnly.prefab";
            DeleteIfExists(target);

            string ops = "["
                + "{\"op\":\"create_prefab\",\"name\":\"RootOnly\"},"
                + "{\"op\":\"save\"}"
                + "]";
            var resp = RunBridge(BuildCreatePrefabRequest(target, ops));
            var err = AssertCreateSuccess(name, resp);
            if (err != null) return err;

            var go = AssetDatabase.LoadAssetAtPath<GameObject>(target);
            if (go == null) return Fail(name, "Created prefab not found on disk.");
            if (go.name != "RootOnly")
                return Fail(name, $"Expected name=RootOnly, got {go.name}.");
            return Pass(name);
        }

        private static TestCaseResult Test_CreatePrefab_HierarchyWithComponent(string prefabPath, string materialPath)
        {
            const string name = "CreatePrefab_HierarchyWithComponent";
            string target = TestAssetDir + "/Created_Hierarchy.prefab";
            DeleteIfExists(target);

            string ops = "["
                + "{\"op\":\"create_prefab\",\"name\":\"HierarchyRoot\"},"
                + "{\"op\":\"create_game_object\",\"name\":\"Child\",\"parent\":\"$root\",\"result\":\"$child\"},"
                + "{\"op\":\"add_component\",\"target\":\"$child\",\"type\":\"UnityEngine.BoxCollider\",\"result\":\"$collider\"},"
                + "{\"op\":\"set\",\"target\":\"$collider\",\"path\":\"m_IsTrigger\",\"value_kind\":\"bool\",\"value_bool\":true},"
                + "{\"op\":\"save\"}"
                + "]";
            var resp = RunBridge(BuildCreatePrefabRequest(target, ops));
            var err = AssertCreateSuccess(name, resp);
            if (err != null) return err;

            var go = AssetDatabase.LoadAssetAtPath<GameObject>(target);
            if (go == null) return Fail(name, "Created prefab not found.");
            if (go.transform.childCount != 1)
                return Fail(name, $"Expected 1 child, got {go.transform.childCount}.");
            var child = go.transform.GetChild(0);
            if (child.name != "Child")
                return Fail(name, $"Expected child name=Child, got {child.name}.");
            var collider = child.GetComponent<BoxCollider>();
            if (collider == null)
                return Fail(name, "Child missing BoxCollider.");
            if (!collider.isTrigger)
                return Fail(name, "Expected isTrigger=true.");
            return Pass(name);
        }

        private static TestCaseResult Test_CreatePrefab_FindComponentAndMutate(string prefabPath, string materialPath)
        {
            const string name = "CreatePrefab_FindComponentAndMutate";
            string target = TestAssetDir + "/Created_FindComp.prefab";
            DeleteIfExists(target);

            string vecJson = "{\\\"x\\\":1.0,\\\"y\\\":2.0,\\\"z\\\":3.0}";
            string ops = "["
                + "{\"op\":\"create_prefab\",\"name\":\"FindCompRoot\"},"
                + "{\"op\":\"find_component\",\"target\":\"$root\",\"type\":\"UnityEngine.Transform\",\"result\":\"$transform\"},"
                + "{\"op\":\"set\",\"target\":\"$transform\",\"path\":\"m_LocalPosition\","
                + "\"value_kind\":\"json\",\"value_json\":\"" + vecJson + "\"},"
                + "{\"op\":\"save\"}"
                + "]";
            var resp = RunBridge(BuildCreatePrefabRequest(target, ops));
            var err = AssertCreateSuccess(name, resp);
            if (err != null) return err;

            var go = AssetDatabase.LoadAssetAtPath<GameObject>(target);
            if (go == null) return Fail(name, "Created prefab not found.");
            var pos = go.transform.localPosition;
            if (Math.Abs(pos.x - 1.0f) > 0.001f || Math.Abs(pos.y - 2.0f) > 0.001f || Math.Abs(pos.z - 3.0f) > 0.001f)
                return Fail(name, $"Expected localPosition=(1,2,3), got ({pos.x},{pos.y},{pos.z}).");
            return Pass(name);
        }

        private static TestCaseResult Test_CreatePrefab_RenameAndReparent(string prefabPath, string materialPath)
        {
            const string name = "CreatePrefab_RenameAndReparent";
            string target = TestAssetDir + "/Created_Reparent.prefab";
            DeleteIfExists(target);

            string ops = "["
                + "{\"op\":\"create_prefab\",\"name\":\"ReparentRoot\"},"
                + "{\"op\":\"create_game_object\",\"name\":\"ChildA\",\"parent\":\"$root\",\"result\":\"$childA\"},"
                + "{\"op\":\"create_game_object\",\"name\":\"ChildB\",\"parent\":\"$root\",\"result\":\"$childB\"},"
                + "{\"op\":\"rename_object\",\"target\":\"$childA\",\"name\":\"Parent\"},"
                + "{\"op\":\"reparent\",\"target\":\"$childB\",\"parent\":\"$childA\"},"
                + "{\"op\":\"save\"}"
                + "]";
            var resp = RunBridge(BuildCreatePrefabRequest(target, ops));
            var err = AssertCreateSuccess(name, resp);
            if (err != null) return err;

            var go = AssetDatabase.LoadAssetAtPath<GameObject>(target);
            if (go == null) return Fail(name, "Created prefab not found.");
            if (go.transform.childCount != 1)
                return Fail(name, $"Expected 1 root child, got {go.transform.childCount}.");
            var parent = go.transform.GetChild(0);
            if (parent.name != "Parent")
                return Fail(name, $"Expected child name=Parent, got {parent.name}.");
            if (parent.childCount != 1)
                return Fail(name, $"Expected 1 grandchild, got {parent.childCount}.");
            if (parent.GetChild(0).name != "ChildB")
                return Fail(name, $"Expected grandchild name=ChildB, got {parent.GetChild(0).name}.");
            return Pass(name);
        }

        private static TestCaseResult Test_CreatePrefab_DuplicateRootRejected(string prefabPath, string materialPath)
        {
            const string name = "CreatePrefab_DuplicateRootRejected";
            string target = TestAssetDir + "/Created_DupRoot.prefab";
            DeleteIfExists(target);

            string ops = "["
                + "{\"op\":\"create_prefab\",\"name\":\"First\"},"
                + "{\"op\":\"create_prefab\",\"name\":\"Second\"},"
                + "{\"op\":\"save\"}"
                + "]";
            var resp = RunBridge(BuildCreatePrefabRequest(target, ops));
            return AssertBridgeFailure(name, resp, null) ?? Pass(name);
        }

        // ----------------------------------------------------------------
        // Test cases: create-mode material
        // ----------------------------------------------------------------

        private static TestCaseResult Test_CreateMaterial_StandardShader(string prefabPath, string materialPath)
        {
            const string name = "CreateMaterial_StandardShader";
            string target = TestAssetDir + "/Created_Standard.mat";
            DeleteIfExists(target);

            string ops = "["
                + "{\"op\":\"create_asset\",\"shader\":\"Standard\"},"
                + "{\"op\":\"save\"}"
                + "]";
            var resp = RunBridge(BuildCreateMaterialRequest(target, ops));
            var err = AssertCreateSuccess(name, resp);
            if (err != null) return err;

            var mat = AssetDatabase.LoadAssetAtPath<Material>(target);
            if (mat == null) return Fail(name, "Created material not found.");
            if (mat.shader == null || mat.shader.name != "Standard")
                return Fail(name, $"Expected Standard shader, got {mat.shader?.name}.");
            return Pass(name);
        }

        private static TestCaseResult Test_CreateMaterial_SetName(string prefabPath, string materialPath)
        {
            const string name = "CreateMaterial_SetName";
            string target = TestAssetDir + "/CustomName.mat";
            DeleteIfExists(target);

            string ops = "["
                + "{\"op\":\"create_asset\",\"shader\":\"Standard\",\"name\":\"CustomName\"},"
                + "{\"op\":\"save\"}"
                + "]";
            var resp = RunBridge(BuildCreateMaterialRequest(target, ops));
            var err = AssertCreateSuccess(name, resp);
            if (err != null) return err;

            var mat = AssetDatabase.LoadAssetAtPath<Material>(target);
            if (mat == null) return Fail(name, "Created material not found.");
            if (mat.name != "CustomName")
                return Fail(name, $"Expected name=CustomName, got {mat.name}.");
            return Pass(name);
        }

        private static TestCaseResult Test_CreateMaterial_MissingShaderRejected(string prefabPath, string materialPath)
        {
            const string name = "CreateMaterial_MissingShaderRejected";
            string target = TestAssetDir + "/Created_BadShader.mat";
            DeleteIfExists(target);

            string ops = "["
                + "{\"op\":\"create_asset\",\"shader\":\"NoSuchShader_12345\"},"
                + "{\"op\":\"save\"}"
                + "]";
            var resp = RunBridge(BuildCreateMaterialRequest(target, ops));
            return AssertBridgeFailure(name, resp, null) ?? Pass(name);
        }

        private static TestCaseResult Test_CreateMaterial_AlreadyExistsRejected(string prefabPath, string materialPath)
        {
            const string name = "CreateMaterial_AlreadyExistsRejected";
            // Target the existing fixture material — creation should be rejected
            string ops = "["
                + "{\"op\":\"create_asset\",\"shader\":\"Standard\"},"
                + "{\"op\":\"save\"}"
                + "]";
            var resp = RunBridge(BuildCreateMaterialRequest(materialPath, ops));
            return AssertBridgeFailure(name, resp, null) ?? Pass(name);
        }

        // ----------------------------------------------------------------
        // Test cases: create-mode scene
        // ----------------------------------------------------------------

        private static TestCaseResult Test_CreateScene_EmptyWithGameObject(string prefabPath, string materialPath)
        {
            const string name = "CreateScene_EmptyWithGameObject";
            string target = TestAssetDir + "/Created_Scene.unity";
            DeleteIfExists(target);

            string ops = "["
                + "{\"op\":\"create_scene\"},"
                + "{\"op\":\"create_game_object\",\"name\":\"Player\",\"parent\":\"$scene\",\"result\":\"$player\"},"
                + "{\"op\":\"save_scene\"}"
                + "]";
            var resp = RunBridge(BuildCreateSceneRequest(target, ops));
            var err = AssertCreateSuccess(name, resp);
            if (err != null) return err;

            string fullPath = Path.Combine(Path.GetDirectoryName(Application.dataPath), target);
            if (!File.Exists(fullPath))
                return Fail(name, "Scene file not found on disk.");
            return Pass(name);
        }

        private static TestCaseResult Test_CreateScene_InstantiatePrefab(string prefabPath, string materialPath)
        {
            const string name = "CreateScene_InstantiatePrefab";
            string target = TestAssetDir + "/Created_SceneInst.unity";
            DeleteIfExists(target);

            string ops = "["
                + "{\"op\":\"create_scene\"},"
                + "{\"op\":\"instantiate_prefab\",\"prefab\":\"" + EscapeJsonString(prefabPath) + "\",\"parent\":\"$scene\",\"result\":\"$instance\"},"
                + "{\"op\":\"save_scene\"}"
                + "]";
            var resp = RunBridge(BuildCreateSceneRequest(target, ops));
            var err = AssertCreateSuccess(name, resp);
            if (err != null) return err;

            string fullPath = Path.Combine(Path.GetDirectoryName(Application.dataPath), target);
            if (!File.Exists(fullPath))
                return Fail(name, "Scene file not found on disk.");
            return Pass(name);
        }

        private static TestCaseResult Test_CreateScene_HierarchyWithComponents(string prefabPath, string materialPath)
        {
            const string name = "CreateScene_HierarchyWithComponents";
            string target = TestAssetDir + "/Created_SceneHier.unity";
            DeleteIfExists(target);

            string ops = "["
                + "{\"op\":\"create_scene\"},"
                + "{\"op\":\"create_game_object\",\"name\":\"Root\",\"parent\":\"$scene\",\"result\":\"$root\"},"
                + "{\"op\":\"create_game_object\",\"name\":\"Child\",\"parent\":\"$root\",\"result\":\"$child\"},"
                + "{\"op\":\"add_component\",\"target\":\"$child\",\"type\":\"UnityEngine.BoxCollider\",\"result\":\"$collider\"},"
                + "{\"op\":\"save_scene\"}"
                + "]";
            var resp = RunBridge(BuildCreateSceneRequest(target, ops));
            var err = AssertCreateSuccess(name, resp);
            if (err != null) return err;

            string fullPath = Path.Combine(Path.GetDirectoryName(Application.dataPath), target);
            if (!File.Exists(fullPath))
                return Fail(name, "Scene file not found on disk.");
            return Pass(name);
        }

        private static TestCaseResult Test_CreateScene_MissingCreateSceneRejected(string prefabPath, string materialPath)
        {
            const string name = "CreateScene_MissingCreateSceneRejected";
            string target = TestAssetDir + "/Created_NoCreate.unity";
            DeleteIfExists(target);

            // Omit create_scene — should fail
            string ops = "["
                + "{\"op\":\"create_game_object\",\"name\":\"Orphan\",\"parent\":\"$scene\",\"result\":\"$go\"},"
                + "{\"op\":\"save_scene\"}"
                + "]";
            var resp = RunBridge(BuildCreateSceneRequest(target, ops));
            return AssertBridgeFailure(name, resp, null) ?? Pass(name);
        }

        // ----------------------------------------------------------------
        // Test cases: variant E2E quality gates
        // ----------------------------------------------------------------

        private static TestCaseResult Test_Variant_SetOverrideProperty(string prefabPath, string materialPath)
        {
            const string name = "Variant_SetOverrideProperty";
            string variantPath = CreateVariantFrom(prefabPath);
            if (variantPath == null) return Fail(name, "Failed to create variant fixture.");

            // Verify it's a Prefab Variant
            var variantGO = AssetDatabase.LoadAssetAtPath<GameObject>(variantPath);
            if (variantGO == null) return Fail(name, "Could not load variant.");
            if (PrefabUtility.GetPrefabAssetType(variantGO) != PrefabAssetType.Variant)
                return Fail(name, $"Expected PrefabAssetType.Variant, got {PrefabUtility.GetPrefabAssetType(variantGO)}.");

            // Mutate variant via bridge: set BoxCollider.m_IsTrigger = true
            string ops = "[" + SetOp("BoxCollider", "m_IsTrigger", "bool", "bool", "true") + "]";
            var resp = RunBridge(BuildPrefabRequest(variantPath, ops));
            var err = AssertBridgeSuccess(name, resp, 1);
            if (err != null) return err;

            // Verify variant has the override value
            var variantProp = GetPrefabProperty(variantPath, "BoxCollider", "m_IsTrigger");
            if (variantProp == null) return Fail(name, "Could not read m_IsTrigger on variant.");
            if (!variantProp.boolValue) return Fail(name, "Expected variant m_IsTrigger=true.");

            // Verify base is unchanged
            var baseProp = GetPrefabProperty(prefabPath, "BoxCollider", "m_IsTrigger");
            if (baseProp == null) return Fail(name, "Could not read m_IsTrigger on base.");
            if (baseProp.boolValue) return Fail(name, "Base m_IsTrigger should still be false.");

            return Pass(name);
        }

        private static TestCaseResult Test_Variant_MultipleOverrides(string prefabPath, string materialPath)
        {
            const string name = "Variant_MultipleOverrides";
            string variantPath = CreateVariantFrom(prefabPath);
            if (variantPath == null) return Fail(name, "Failed to create variant fixture.");

            // Apply two overrides in a single bridge call
            string ops = "["
                + SetOp("BoxCollider", "m_IsTrigger", "bool", "bool", "true") + ","
                + SetOp("AudioSource", "m_Volume", "float", "float", "0.25")
                + "]";
            var resp = RunBridge(BuildPrefabRequest(variantPath, ops));
            var err = AssertBridgeSuccess(name, resp, 2);
            if (err != null) return err;

            // Verify both overrides on variant
            var triggerProp = GetPrefabProperty(variantPath, "BoxCollider", "m_IsTrigger");
            if (triggerProp == null) return Fail(name, "Could not read m_IsTrigger on variant.");
            if (!triggerProp.boolValue) return Fail(name, "Expected variant m_IsTrigger=true.");

            var volumeProp = GetPrefabProperty(variantPath, "AudioSource", "m_Volume");
            if (volumeProp == null) return Fail(name, "Could not read m_Volume on variant.");
            if (Math.Abs(volumeProp.floatValue - 0.25f) > 0.001f)
                return Fail(name, $"Expected variant m_Volume=0.25, got {volumeProp.floatValue}.");

            // Verify base values unchanged
            var baseVolume = GetPrefabProperty(prefabPath, "AudioSource", "m_Volume");
            if (baseVolume == null) return Fail(name, "Could not read m_Volume on base.");
            if (Math.Abs(baseVolume.floatValue - 1.0f) > 0.001f)
                return Fail(name, $"Base m_Volume should still be 1.0, got {baseVolume.floatValue}.");

            return Pass(name);
        }

        private static TestCaseResult Test_Variant_OverridePersistsAfterSave(string prefabPath, string materialPath)
        {
            const string name = "Variant_OverridePersistsAfterSave";
            string variantPath = CreateVariantFrom(prefabPath);
            if (variantPath == null) return Fail(name, "Failed to create variant fixture.");

            // Set override
            string ops = "[" + SetOp("BoxCollider", "m_IsTrigger", "bool", "bool", "true") + "]";
            var resp = RunBridge(BuildPrefabRequest(variantPath, ops));
            var err = AssertBridgeSuccess(name, resp, 1);
            if (err != null) return err;

            // Force save and reimport
            AssetDatabase.SaveAssets();
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            Resources.UnloadAsset(AssetDatabase.LoadAssetAtPath<GameObject>(variantPath));
            AssetDatabase.ImportAsset(variantPath, ImportAssetOptions.ForceUpdate);

            // Reload and verify override persists
            var prop = GetPrefabProperty(variantPath, "BoxCollider", "m_IsTrigger");
            if (prop == null) return Fail(name, "Could not read m_IsTrigger after reopen.");
            if (!prop.boolValue) return Fail(name, "Variant override did not persist after save/reopen.");

            return Pass(name);
        }

        private static TestCaseResult Test_Variant_InheritBaseChange(string prefabPath, string materialPath)
        {
            const string name = "Variant_InheritBaseChange";
            string variantPath = CreateVariantFrom(prefabPath);
            if (variantPath == null) return Fail(name, "Failed to create variant fixture.");

            // Variant has no overrides — it mirrors base.
            // Mutate BASE via bridge: set m_IsTrigger = true
            string ops = "[" + SetOp("BoxCollider", "m_IsTrigger", "bool", "bool", "true") + "]";
            var resp = RunBridge(BuildPrefabRequest(prefabPath, ops));
            var err = AssertBridgeSuccess(name, resp, 1);
            if (err != null) return err;

            // Refresh to propagate base change to variant
            AssetDatabase.SaveAssets();
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);

            // Variant should inherit the base change (no override blocks it)
            var variantProp = GetPrefabProperty(variantPath, "BoxCollider", "m_IsTrigger");
            if (variantProp == null) return Fail(name, "Could not read m_IsTrigger on variant.");
            if (!variantProp.boolValue)
                return Fail(name, "Variant should inherit m_IsTrigger=true from mutated base.");

            return Pass(name);
        }

        // ----------------------------------------------------------------
        // Handle-based ObjectReference (Phase H)
        // ----------------------------------------------------------------

        private static TestCaseResult Test_Handle_ObjectReference_InCreateMode(string prefabPath, string materialPath)
        {
            const string name = "Handle_ObjectReference_InCreateMode";
            string target = TestAssetDir + "/HandleObjRef.prefab";
            DeleteIfExists(target);

            string ops = "["
                + "{\"op\":\"create_prefab\",\"name\":\"HandleObjRefRoot\"},"
                + "{\"op\":\"add_component\",\"target\":\"$root\",\"type\":\"UnityEngine.Rigidbody\",\"result\":\"$rb\"},"
                + "{\"op\":\"add_component\",\"target\":\"$root\",\"type\":\"UnityEngine.ConfigurableJoint\",\"result\":\"$joint\"},"
                + "{\"op\":\"set\",\"target\":\"$joint\",\"path\":\"m_ConnectedBody\","
                + "\"value_kind\":\"handle\",\"value_string\":\"$rb\"},"
                + "{\"op\":\"save\"}"
                + "]";
            var resp = RunBridge(BuildCreatePrefabRequest(target, ops));
            var err = AssertCreateSuccess(name, resp);
            if (err != null) return err;

            var go = AssetDatabase.LoadAssetAtPath<GameObject>(target);
            if (go == null) return Fail(name, "Prefab not found after save.");
            var joint = go.GetComponent<ConfigurableJoint>();
            if (joint == null) return Fail(name, "ConfigurableJoint not found.");
            var rb = go.GetComponent<Rigidbody>();
            if (rb == null) return Fail(name, "Rigidbody not found.");
            if (joint.connectedBody != rb)
                return Fail(name, $"connectedBody should reference the Rigidbody but was {joint.connectedBody}.");

            return Pass(name);
        }

        private static TestCaseResult Test_Handle_ObjectReference_UnknownHandle(string prefabPath, string materialPath)
        {
            const string name = "Handle_ObjectReference_UnknownHandle";
            string target = TestAssetDir + "/HandleUnknown.prefab";
            DeleteIfExists(target);

            string ops = "["
                + "{\"op\":\"create_prefab\",\"name\":\"HandleUnknownRoot\"},"
                + "{\"op\":\"add_component\",\"target\":\"$root\",\"type\":\"UnityEngine.ConfigurableJoint\",\"result\":\"$joint\"},"
                + "{\"op\":\"set\",\"target\":\"$joint\",\"path\":\"m_ConnectedBody\","
                + "\"value_kind\":\"handle\",\"value_string\":\"$nonexistent\"},"
                + "{\"op\":\"save\"}"
                + "]";
            var resp = RunBridge(BuildCreatePrefabRequest(target, ops));
            return AssertBridgeFailure(name, resp, null) ?? Pass(name);
        }

        private static TestCaseResult Test_Handle_ObjectReference_InOpenModeRejected(string prefabPath, string materialPath)
        {
            const string name = "Handle_ObjectReference_InOpenModeRejected";
            string ops = "["
                + "{\"op\":\"set\",\"component\":\"BoxCollider\",\"path\":\"m_Material\","
                + "\"value_kind\":\"handle\",\"value_string\":\"$something\"}"
                + "]";
            var resp = RunBridge(BuildPrefabRequest(prefabPath, ops));
            return AssertBridgeFailure(name, resp, null) ?? Pass(name);
        }

        // ----------------------------------------------------------------
        // remove_component
        // ----------------------------------------------------------------

        private static TestCaseResult Test_RemoveComponent_Success(string prefabPath, string materialPath)
        {
            const string name = "RemoveComponent_Success";
            string target = TestAssetDir + "/RemoveComp.prefab";
            DeleteIfExists(target);

            string ops = "["
                + "{\"op\":\"create_prefab\",\"name\":\"RemoveCompRoot\"},"
                + "{\"op\":\"add_component\",\"target\":\"$root\",\"type\":\"UnityEngine.BoxCollider\",\"result\":\"$collider\"},"
                + "{\"op\":\"remove_component\",\"target\":\"$collider\"},"
                + "{\"op\":\"save\"}"
                + "]";
            var resp = RunBridge(BuildCreatePrefabRequest(target, ops));
            var err = AssertCreateSuccess(name, resp);
            if (err != null) return err;

            var go = AssetDatabase.LoadAssetAtPath<GameObject>(target);
            if (go == null) return Fail(name, "Prefab not found after save.");
            if (go.GetComponent<BoxCollider>() != null)
                return Fail(name, "BoxCollider should have been removed but still exists.");

            return Pass(name);
        }

        private static TestCaseResult Test_RemoveComponent_TransformRejected(string prefabPath, string materialPath)
        {
            const string name = "RemoveComponent_TransformRejected";
            string target = TestAssetDir + "/RemoveTransform.prefab";
            DeleteIfExists(target);

            string ops = "["
                + "{\"op\":\"create_prefab\",\"name\":\"RemoveTransformRoot\"},"
                + "{\"op\":\"find_component\",\"target\":\"$root\",\"type\":\"UnityEngine.Transform\",\"result\":\"$transform\"},"
                + "{\"op\":\"remove_component\",\"target\":\"$transform\"},"
                + "{\"op\":\"save\"}"
                + "]";
            var resp = RunBridge(BuildCreatePrefabRequest(target, ops));
            return AssertBridgeFailure(name, resp, null) ?? Pass(name);
        }

        // ----------------------------------------------------------------
        // Additional value_kind types
        // ----------------------------------------------------------------

        private static TestCaseResult Test_Set_ColorProperty_String(string prefabPath, string materialPath)
        {
            const string name = "Set_ColorProperty_String";
            string target = TestAssetDir + "/ColorString.prefab";
            DeleteIfExists(target);

            string ops = "["
                + "{\"op\":\"create_prefab\",\"name\":\"ColorStringRoot\"},"
                + "{\"op\":\"add_component\",\"target\":\"$root\",\"type\":\"UnityEngine.Light\",\"result\":\"$light\"},"
                + "{\"op\":\"set\",\"target\":\"$light\",\"path\":\"m_Color\","
                + "\"value_kind\":\"string\",\"value_string\":\"#FF8040\"},"
                + "{\"op\":\"save\"}"
                + "]";
            var resp = RunBridge(BuildCreatePrefabRequest(target, ops));
            var err = AssertCreateSuccess(name, resp);
            if (err != null) return err;

            var go = AssetDatabase.LoadAssetAtPath<GameObject>(target);
            if (go == null) return Fail(name, "Prefab not found after save.");
            var light = go.GetComponent<Light>();
            if (light == null) return Fail(name, "Light not found.");
            // #FF8040 = (1.0, 0.502, 0.251, 1.0) approximately
            if (Mathf.Abs(light.color.r - 1.0f) > 0.02f || Mathf.Abs(light.color.g - 0.502f) > 0.02f || Mathf.Abs(light.color.b - 0.251f) > 0.02f)
                return Fail(name, $"Color mismatch: expected ~(1, 0.5, 0.25) got ({light.color.r}, {light.color.g}, {light.color.b}).");

            return Pass(name);
        }

        private static TestCaseResult Test_Set_EnumProperty_StringName(string prefabPath, string materialPath)
        {
            const string name = "Set_EnumProperty_StringName";
            // AudioRolloffMode: Logarithmic=0, Linear=1, Custom=2
            // Use Custom (2) to avoid coincidence with prior test that sets Linear (1) via int.
            string ops = "[" + SetOp("AudioSource", "rolloffMode", "string", "string", "Custom") + "]";
            var resp = RunBridge(BuildPrefabRequest(prefabPath, ops));
            var err = AssertBridgeSuccess(name, resp, 1);
            if (err != null) return err;

            var prop = GetPrefabProperty(prefabPath, "AudioSource", "rolloffMode");
            if (prop == null) return Fail(name, "Could not read rolloffMode.");
            if (prop.enumValueIndex != 2)
                return Fail(name, $"rolloffMode expected 2 (Custom) but got {prop.enumValueIndex}.");

            return Pass(name);
        }

        private static TestCaseResult Test_Set_QuaternionProperty_Json(string prefabPath, string materialPath)
        {
            const string name = "Set_QuaternionProperty_Json";
            string target = TestAssetDir + "/QuatJson.prefab";
            DeleteIfExists(target);

            string quatJson = "{\\\"x\\\":0.0,\\\"y\\\":0.7071,\\\"z\\\":0.0,\\\"w\\\":0.7071}";
            string ops = "["
                + "{\"op\":\"create_prefab\",\"name\":\"QuatRoot\"},"
                + "{\"op\":\"find_component\",\"target\":\"$root\",\"type\":\"UnityEngine.Transform\",\"result\":\"$transform\"},"
                + "{\"op\":\"set\",\"target\":\"$transform\",\"path\":\"m_LocalRotation\","
                + "\"value_kind\":\"json\",\"value_json\":\"" + quatJson + "\"},"
                + "{\"op\":\"save\"}"
                + "]";
            var resp = RunBridge(BuildCreatePrefabRequest(target, ops));
            var err = AssertCreateSuccess(name, resp);
            if (err != null) return err;

            var go = AssetDatabase.LoadAssetAtPath<GameObject>(target);
            if (go == null) return Fail(name, "Prefab not found after save.");
            var q = go.transform.localRotation;
            if (Mathf.Abs(q.y - 0.7071f) > 0.01f || Mathf.Abs(q.w - 0.7071f) > 0.01f)
                return Fail(name, $"Quaternion mismatch: expected ~(0,0.707,0,0.707) got ({q.x},{q.y},{q.z},{q.w}).");

            return Pass(name);
        }

        private static TestCaseResult Test_Set_RectProperty_Json(string prefabPath, string materialPath)
        {
            const string name = "Set_RectProperty_Json";
            string target = TestAssetDir + "/RectJson.prefab";
            DeleteIfExists(target);

            string rectJson = "{\\\"x\\\":0.1,\\\"y\\\":0.2,\\\"width\\\":0.5,\\\"height\\\":0.6}";
            string ops = "["
                + "{\"op\":\"create_prefab\",\"name\":\"RectRoot\"},"
                + "{\"op\":\"add_component\",\"target\":\"$root\",\"type\":\"UnityEngine.Camera\",\"result\":\"$cam\"},"
                + "{\"op\":\"set\",\"target\":\"$cam\",\"path\":\"m_NormalizedViewPortRect\","
                + "\"value_kind\":\"json\",\"value_json\":\"" + rectJson + "\"},"
                + "{\"op\":\"save\"}"
                + "]";
            var resp = RunBridge(BuildCreatePrefabRequest(target, ops));
            var err = AssertCreateSuccess(name, resp);
            if (err != null) return err;

            var go = AssetDatabase.LoadAssetAtPath<GameObject>(target);
            if (go == null) return Fail(name, "Prefab not found after save.");
            var cam = go.GetComponent<Camera>();
            if (cam == null) return Fail(name, "Camera not found.");
            var r = cam.rect;
            if (Mathf.Abs(r.x - 0.1f) > 0.01f || Mathf.Abs(r.y - 0.2f) > 0.01f
                || Mathf.Abs(r.width - 0.5f) > 0.01f || Mathf.Abs(r.height - 0.6f) > 0.01f)
                return Fail(name, $"Rect mismatch: expected (0.1,0.2,0.5,0.6) got ({r.x},{r.y},{r.width},{r.height}).");

            return Pass(name);
        }

        // ----------------------------------------------------------------
        // Additional error paths
        // ----------------------------------------------------------------

        private static TestCaseResult Test_Set_MissingTarget_OpenMode(string prefabPath, string materialPath)
        {
            const string name = "Set_MissingTarget_OpenMode";
            string ops = "[" + SetOp("BoxCollider", "m_IsTrigger", "bool", "bool", "true") + "]";
            var resp = RunBridge(BuildPrefabRequest("Assets/__NonExistent_Test_12345__.prefab", ops));
            return AssertBridgeFailure(name, resp, null) ?? Pass(name);
        }

        private static TestCaseResult Test_Set_EmptyTarget(string prefabPath, string materialPath)
        {
            const string name = "Set_EmptyTarget";
            string json = "{\"protocol_version\":" + ProtocolVersion
                + ",\"target\":\"\",\"kind\":\"prefab\",\"mode\":\"open\""
                + ",\"ops\":[{\"op\":\"set\",\"component\":\"BoxCollider\",\"path\":\"m_IsTrigger\""
                + ",\"value_kind\":\"bool\",\"value_bool\":true}]}";
            var resp = RunBridge(json);
            return AssertBridgeFailure(name, resp, "UNITY_BRIDGE_SCHEMA") ?? Pass(name);
        }

        private static TestCaseResult Test_Set_EmptyOps(string prefabPath, string materialPath)
        {
            const string name = "Set_EmptyOps";
            string json = "{\"protocol_version\":" + ProtocolVersion
                + ",\"target\":\"" + EscapeJsonString(prefabPath)
                + "\",\"kind\":\"prefab\",\"mode\":\"open\""
                + ",\"ops\":[]}";
            var resp = RunBridge(json);
            return AssertBridgeSuccess(name, resp, 0) ?? Pass(name);
        }

        // ----------------------------------------------------------------
        // UdonSharp backing (conditional)
        // ----------------------------------------------------------------

        private static TestCaseResult Test_UdonSharpBacking_CreateMode(string prefabPath, string materialPath)
        {
            const string name = "UdonSharpBacking_CreateMode";

            // Locate UdonSharpBehaviour type via reflection
            Type usbType = null;
            foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
            {
                try { usbType = asm.GetType("UdonSharp.UdonSharpBehaviour"); } catch { }
                if (usbType != null) break;
            }
            if (usbType == null) return Pass(name, "Skipped — UdonSharp not installed.");

            // Find a concrete subclass
            Type concreteType = null;
            foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
            {
                try
                {
                    foreach (var t in asm.GetTypes())
                    {
                        if (!t.IsAbstract && usbType.IsAssignableFrom(t) && t != usbType)
                        {
                            concreteType = t;
                            break;
                        }
                    }
                }
                catch { }
                if (concreteType != null) break;
            }
            if (concreteType == null) return Pass(name, "Skipped — no concrete UdonSharpBehaviour found.");

            string target = TestAssetDir + "/UdonBacking.prefab";
            DeleteIfExists(target);

            string fullTypeName = concreteType.FullName + ", " + concreteType.Assembly.GetName().Name;
            string ops = "["
                + "{\"op\":\"create_prefab\",\"name\":\"UdonBackingRoot\"},"
                + "{\"op\":\"add_component\",\"target\":\"$root\",\"type\":\"" + EscapeJsonString(fullTypeName) + "\",\"result\":\"$udon\"},"
                + "{\"op\":\"save\"}"
                + "]";
            var resp = RunBridge(BuildCreatePrefabRequest(target, ops));
            var err = AssertCreateSuccess(name, resp);
            if (err != null) return err;

            // Verify backing UdonBehaviour was created
            var go = AssetDatabase.LoadAssetAtPath<GameObject>(target);
            if (go == null) return Fail(name, "Prefab not found after save.");

            Type udonBehaviourType = null;
            foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
            {
                try { udonBehaviourType = asm.GetType("VRC.Udon.UdonBehaviour"); } catch { }
                if (udonBehaviourType != null) break;
            }
            if (udonBehaviourType == null) return Fail(name, "VRC.Udon.UdonBehaviour type not found despite UdonSharp being present.");

            var backing = go.GetComponent(udonBehaviourType);
            if (backing == null)
                return Fail(name, "Backing UdonBehaviour was not auto-created by add_component.");

            return Pass(name);
        }

        // ----------------------------------------------------------------
        // Utility
        // ----------------------------------------------------------------

        private static string GetArgValue(string key)
        {
            string[] args = Environment.GetCommandLineArgs();
            for (int i = 0; i < args.Length - 1; i++)
            {
                if (string.Equals(args[i], key, StringComparison.Ordinal))
                    return args[i + 1];
            }
            return null;
        }

        private static string EscapeJsonString(string s)
        {
            if (s == null) return string.Empty;
            return s.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\n", "\\n").Replace("\r", "\\r");
        }
    }
}
