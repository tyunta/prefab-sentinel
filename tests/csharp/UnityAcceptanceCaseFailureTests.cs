using System;
using System.Text.Json;
using Xunit;

namespace PrefabSentinel.Tests;

public class UnityAcceptanceCaseFailureTests
{
    [Fact]
    public void Serialized_Public_Failure_Does_Not_Contain_Exception_Detail()
    {
        const string privateDetail =
            "Invalid fixture at C:\\Users\\operator\\SecretWorld.prefab";
        var exception = new InvalidOperationException(privateDetail);

        UnityAcceptanceCaseFailure failure =
            UnityAcceptanceCaseFailure.FromException(
                "Acceptance_TransformMutationReadback",
                exception);
        string json = JsonSerializer.Serialize(failure);

        Assert.Equal("ACCEPTANCE_CASE_EXCEPTION", failure.Code);
        Assert.Equal(
            "Acceptance_TransformMutationReadback failed unexpectedly; "
                + "inspect the Unity Console for local exception detail.",
            failure.Message);
        Assert.DoesNotContain(privateDetail, json, StringComparison.Ordinal);
        Assert.DoesNotContain(
            nameof(InvalidOperationException),
            json,
            StringComparison.Ordinal);
        Assert.DoesNotContain("SecretWorld.prefab", json, StringComparison.Ordinal);
    }
}
