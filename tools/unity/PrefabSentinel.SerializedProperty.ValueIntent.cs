using System.Globalization;

namespace PrefabSentinel
{
    internal enum SerializedPropertyValueIntentKind
    {
        None,
        Bool,
        Int,
        Long,
        Float,
        String,
        EnumName,
        EnumIndex,
        ObjectReferenceAssetPath,
        ObjectReferenceHierarchyPath,
        ObjectReferenceNull,
        ArraySize,
    }

    internal readonly struct SerializedPropertyValueIntent
    {
        internal const string ValueRequiredCode =
            "EDITOR_CTRL_SERIALIZED_PROPERTY_VALUE_REQUIRED";
        internal const string ValueConflictCode =
            "EDITOR_CTRL_SERIALIZED_PROPERTY_VALUE_CONFLICT";
        internal const string ObjectReferenceNotFoundCode =
            "EDITOR_CTRL_SERIALIZED_PROPERTY_OBJECT_REF_NOT_FOUND";
        internal const string ArraySizeInvalidCode =
            "EDITOR_CTRL_SERIALIZED_PROPERTY_ARRAY_SIZE_INVALID";

        public bool Success { get; }
        public string ErrorCode { get; }
        public SerializedPropertyValueIntentKind Kind { get; }
        public bool BoolValue { get; }
        public int IntValue { get; }
        public long LongValue { get; }
        public float FloatValue { get; }
        public string StringValue { get; }
        public string EnumName { get; }
        public int EnumIndex { get; }
        public int ArraySize { get; }

        private SerializedPropertyValueIntent(
            bool success,
            string errorCode,
            SerializedPropertyValueIntentKind kind,
            bool boolValue,
            int intValue,
            long longValue,
            float floatValue,
            string stringValue,
            string enumName,
            int enumIndex,
            int arraySize)
        {
            Success = success;
            ErrorCode = errorCode;
            Kind = kind;
            BoolValue = boolValue;
            IntValue = intValue;
            LongValue = longValue;
            FloatValue = floatValue;
            StringValue = stringValue;
            EnumName = enumName;
            EnumIndex = enumIndex;
            ArraySize = arraySize;
        }

        public static SerializedPropertyValueIntent Parse(EditorControlRequest request)
        {
            var selected = SerializedPropertyValueIntentKind.None;
            int count = 0;
            void Select(SerializedPropertyValueIntentKind kind)
            {
                selected = kind;
                count++;
            }

            if (request.serialized_property_bool_value_present)
                Select(SerializedPropertyValueIntentKind.Bool);
            if (request.serialized_property_int_value_present)
                Select(SerializedPropertyValueIntentKind.Int);
            if (request.serialized_property_long_value_present)
                Select(SerializedPropertyValueIntentKind.Long);
            if (request.serialized_property_float_value_present)
                Select(SerializedPropertyValueIntentKind.Float);
            if (request.serialized_property_string_value_present)
                Select(SerializedPropertyValueIntentKind.String);
            if (request.serialized_property_enum_name_present)
                Select(SerializedPropertyValueIntentKind.EnumName);
            if (request.serialized_property_enum_index_present)
                Select(SerializedPropertyValueIntentKind.EnumIndex);
            if (request.serialized_property_object_reference_asset_path_present)
                Select(SerializedPropertyValueIntentKind.ObjectReferenceAssetPath);
            if (request.serialized_property_object_reference_hierarchy_path_present)
                Select(SerializedPropertyValueIntentKind.ObjectReferenceHierarchyPath);
            if (request.serialized_property_object_reference_null)
                Select(SerializedPropertyValueIntentKind.ObjectReferenceNull);
            if (request.serialized_property_array_size_present)
                Select(SerializedPropertyValueIntentKind.ArraySize);

            if (count == 0)
                return Rejected(ValueRequiredCode);
            if (count > 1)
                return Rejected(ValueConflictCode);

            return BuildSelected(request, selected);
        }

        private static SerializedPropertyValueIntent BuildSelected(
            EditorControlRequest request,
            SerializedPropertyValueIntentKind kind)
        {
            switch (kind)
            {
                case SerializedPropertyValueIntentKind.Bool:
                    return Accepted(kind, boolValue: request.serialized_property_bool_value);
                case SerializedPropertyValueIntentKind.Int:
                    return Accepted(kind, intValue: request.serialized_property_int_value);
                case SerializedPropertyValueIntentKind.Long:
                    return Accepted(kind, longValue: request.serialized_property_long_value);
                case SerializedPropertyValueIntentKind.Float:
                    return Accepted(kind, floatValue: request.serialized_property_float_value);
                case SerializedPropertyValueIntentKind.String:
                    return Accepted(kind, stringValue: request.serialized_property_string_value);
                case SerializedPropertyValueIntentKind.EnumName:
                    return Accepted(kind, enumName: request.serialized_property_enum_name);
                case SerializedPropertyValueIntentKind.EnumIndex:
                    return Accepted(kind, enumIndex: request.serialized_property_enum_index);
                case SerializedPropertyValueIntentKind.ObjectReferenceAssetPath:
                    return ObjectReference(
                        kind,
                        request.serialized_property_object_reference_asset_path);
                case SerializedPropertyValueIntentKind.ObjectReferenceHierarchyPath:
                    return ObjectReference(
                        kind,
                        request.serialized_property_object_reference_hierarchy_path);
                case SerializedPropertyValueIntentKind.ObjectReferenceNull:
                    return Accepted(kind);
                case SerializedPropertyValueIntentKind.ArraySize:
                    if (request.serialized_property_array_size < 0)
                        return Rejected(ArraySizeInvalidCode);
                    return Accepted(kind, arraySize: request.serialized_property_array_size);
                default:
                    return Rejected(ValueRequiredCode);
            }
        }

        private static SerializedPropertyValueIntent ObjectReference(
            SerializedPropertyValueIntentKind kind,
            string path)
        {
            if (string.IsNullOrWhiteSpace(path))
                return Rejected(ObjectReferenceNotFoundCode);
            return Accepted(kind);
        }

        private static SerializedPropertyValueIntent Accepted(
            SerializedPropertyValueIntentKind kind,
            bool boolValue = false,
            int intValue = 0,
            long longValue = 0,
            float floatValue = 0f,
            string stringValue = "",
            string enumName = "",
            int enumIndex = 0,
            int arraySize = 0)
        {
            return new SerializedPropertyValueIntent(
                true,
                string.Empty,
                kind,
                boolValue,
                intValue,
                longValue,
                floatValue,
                stringValue,
                enumName,
                enumIndex,
                arraySize);
        }

        private static SerializedPropertyValueIntent Rejected(string code)
        {
            return new SerializedPropertyValueIntent(
                false,
                code,
                SerializedPropertyValueIntentKind.None,
                false,
                0,
                0,
                0f,
                string.Empty,
                string.Empty,
                0,
                0);
        }
    }

    internal readonly struct SerializedPropertyTraversalOptions
    {
        internal const string LimitInvalidCode =
            "EDITOR_CTRL_SERIALIZED_PROPERTY_LIST_LIMIT_INVALID";
        internal const string CursorInvalidCode =
            "EDITOR_CTRL_SERIALIZED_PROPERTY_CURSOR_INVALID";
        internal const int DefaultDepth = 1;
        internal const int DefaultCap = 50;
        internal const int HardCap = 200;

        public bool Success { get; }
        public string ErrorCode { get; }
        public int Depth { get; }
        public int Cap { get; }
        public int Cursor { get; }

        private SerializedPropertyTraversalOptions(
            bool success,
            string errorCode,
            int depth,
            int cap,
            int cursor)
        {
            Success = success;
            ErrorCode = errorCode;
            Depth = depth;
            Cap = cap;
            Cursor = cursor;
        }

        public static SerializedPropertyTraversalOptions Parse(
            int depth,
            int cap,
            string cursor)
        {
            if (depth < 0 || cap < 1 || cap > HardCap)
                return Rejected(LimitInvalidCode);

            if (string.IsNullOrEmpty(cursor))
                return Accepted(depth, cap, cursor: 0);

            if (!int.TryParse(
                    cursor,
                    NumberStyles.None,
                    CultureInfo.InvariantCulture,
                    out int parsedCursor)
                || parsedCursor < 0)
            {
                return Rejected(CursorInvalidCode);
            }

            return Accepted(depth, cap, parsedCursor);
        }

        private static SerializedPropertyTraversalOptions Accepted(
            int depth,
            int cap,
            int cursor)
        {
            return new SerializedPropertyTraversalOptions(
                true, string.Empty, depth, cap, cursor);
        }

        private static SerializedPropertyTraversalOptions Rejected(string code)
        {
            return new SerializedPropertyTraversalOptions(
                false, code, 0, 0, 0);
        }
    }
}
