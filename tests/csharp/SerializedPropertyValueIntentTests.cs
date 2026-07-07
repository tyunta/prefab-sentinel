using PrefabSentinel;
using Xunit;

namespace PrefabSentinel.Tests;

public class SerializedPropertyValueIntentTests
{
    [Fact]
    public void Empty_String_Is_Preserved_As_A_String_Intent()
    {
        var request = new EditorControlRequest
        {
            serialized_property_string_value_present = true,
            serialized_property_string_value = string.Empty,
        };

        SerializedPropertyValueIntent result = SerializedPropertyValueIntent.Parse(request);

        Assert.Equal(
            (true, SerializedPropertyValueIntentKind.String, string.Empty),
            (result.Success, result.Kind, result.StringValue));
    }

    [Fact]
    public void Explicit_Null_Object_Reference_Is_A_Distinct_Intent()
    {
        var request = new EditorControlRequest
        {
            serialized_property_object_reference_null = true,
        };

        SerializedPropertyValueIntent result = SerializedPropertyValueIntent.Parse(request);

        Assert.Equal(
            (true, SerializedPropertyValueIntentKind.ObjectReferenceNull),
            (result.Success, result.Kind));
    }

    [Fact]
    public void Empty_Asset_Reference_Path_Is_Rejected_With_Reference_Code()
    {
        var request = new EditorControlRequest
        {
            serialized_property_object_reference_asset_path_present = true,
            serialized_property_object_reference_asset_path = string.Empty,
        };

        SerializedPropertyValueIntent result = SerializedPropertyValueIntent.Parse(request);

        Assert.Equal(
            (false, SerializedPropertyValueIntent.ObjectReferenceNotFoundCode),
            (result.Success, result.ErrorCode));
    }

    [Fact]
    public void Multiple_Value_Intents_Are_Rejected_As_A_Conflict()
    {
        var request = new EditorControlRequest
        {
            serialized_property_bool_value_present = true,
            serialized_property_bool_value = false,
            serialized_property_int_value_present = true,
            serialized_property_int_value = 0,
        };

        SerializedPropertyValueIntent result = SerializedPropertyValueIntent.Parse(request);

        Assert.Equal(
            (false, SerializedPropertyValueIntent.ValueConflictCode),
            (result.Success, result.ErrorCode));
    }

    [Fact]
    public void False_Boolean_Is_Preserved_When_Its_Presence_Marker_Is_Set()
    {
        var request = new EditorControlRequest
        {
            serialized_property_bool_value_present = true,
            serialized_property_bool_value = false,
        };

        SerializedPropertyValueIntent result = SerializedPropertyValueIntent.Parse(request);

        Assert.Equal(
            (true, SerializedPropertyValueIntentKind.Bool, false),
            (result.Success, result.Kind, result.BoolValue));
    }

    [Fact]
    public void Zero_Array_Size_Is_Preserved_When_Its_Presence_Marker_Is_Set()
    {
        var request = new EditorControlRequest
        {
            serialized_property_array_size_present = true,
            serialized_property_array_size = 0,
        };

        SerializedPropertyValueIntent result = SerializedPropertyValueIntent.Parse(request);

        Assert.Equal(
            (true, SerializedPropertyValueIntentKind.ArraySize, 0),
            (result.Success, result.Kind, result.ArraySize));
    }

    [Fact]
    public void Negative_Array_Size_Is_Rejected_With_Array_Size_Code()
    {
        var request = new EditorControlRequest
        {
            serialized_property_array_size_present = true,
            serialized_property_array_size = -1,
        };

        SerializedPropertyValueIntent result = SerializedPropertyValueIntent.Parse(request);

        Assert.Equal(
            (false, SerializedPropertyValueIntent.ArraySizeInvalidCode),
            (result.Success, result.ErrorCode));
    }
}
