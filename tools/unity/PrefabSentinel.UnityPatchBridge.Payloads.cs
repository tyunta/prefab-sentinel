using System;
using UnityEngine;
namespace PrefabSentinel
{
    public static partial class UnityPatchBridge
    {
        [Serializable]
        private sealed class ColorPayload
        {
            public float r = 0f;
            public float g = 0f;
            public float b = 0f;
            public float a = 1f;
        }
        [Serializable]
        private sealed class Vector2Payload
        {
            public float x = 0f;
            public float y = 0f;
        }
        [Serializable]
        private sealed class Vector3Payload
        {
            public float x = 0f;
            public float y = 0f;
            public float z = 0f;
        }
        [Serializable]
        private sealed class Vector4Payload
        {
            public float x = 0f;
            public float y = 0f;
            public float z = 0f;
            public float w = 0f;
        }
        [Serializable]
        private sealed class QuaternionPayload
        {
            public float x = 0f;
            public float y = 0f;
            public float z = 0f;
            public float w = 1f;
        }
        [Serializable]
        private sealed class ObjectReferencePayload
        {
            public string guid = string.Empty;
            public long fileID = 0;   // Unity native format (plan_generators output)
            public long file_id = 0;  // snake_case format (example plan compat)
        }
        [Serializable]
        private sealed class RectPayload
        {
            public float x = 0f;
            public float y = 0f;
            public float width = 0f;
            public float height = 0f;
        }
        [Serializable]
        private sealed class BoundsPayload
        {
            public Vector3Payload center = new Vector3Payload();
            public Vector3Payload size = new Vector3Payload();
        }
        [Serializable]
        private sealed class Vector2IntPayload
        {
            public int x = 0;
            public int y = 0;
        }
        [Serializable]
        private sealed class Vector3IntPayload
        {
            public int x = 0;
            public int y = 0;
            public int z = 0;
        }
        [Serializable]
        private sealed class RectIntPayload
        {
            public int x = 0;
            public int y = 0;
            public int width = 0;
            public int height = 0;
        }
        [Serializable]
        private sealed class BoundsIntPayload
        {
            public Vector3IntPayload position = new Vector3IntPayload();
            public Vector3IntPayload size = new Vector3IntPayload();
        }
        [Serializable]
        private sealed class AnimationCurvePayload
        {
            public AnimationCurveKeyPayload[] keys = Array.Empty<AnimationCurveKeyPayload>();
            public int pre_wrap_mode = (int)WrapMode.Default;
            public int post_wrap_mode = (int)WrapMode.Default;
        }
        [Serializable]
        private sealed class AnimationCurveKeyPayload
        {
            public float time = 0f;
            public float value = 0f;
            public float in_tangent = 0f;
            public float out_tangent = 0f;
        }
        [Serializable]
        private sealed class GradientPayload
        {
            public GradientColorKeyPayload[] color_keys = Array.Empty<GradientColorKeyPayload>();
            public GradientAlphaKeyPayload[] alpha_keys = Array.Empty<GradientAlphaKeyPayload>();
            public int mode = 0;
        }
        [Serializable]
        private sealed class GradientColorKeyPayload
        {
            public ColorPayload color = new ColorPayload();
            public float time = 0f;
        }
        [Serializable]
        private sealed class GradientAlphaKeyPayload
        {
            public float alpha = 1f;
            public float time = 0f;
        }
        [Serializable]
        private sealed class ManagedReferenceTypeHintPayload
        {
            public string __type = string.Empty;
        }
    }
}
