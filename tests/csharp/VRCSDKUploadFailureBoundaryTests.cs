using System;
using Xunit;

namespace PrefabSentinel.Tests;

public sealed class VRCSDKUploadFailureBoundaryTests
{
    private const string SecretPath = @"C:\Users\ExampleUser\AppData\Local\VRChat\SDK\secret.asset";

    [Fact]
    public void ReportFailure_UsesFixedPublicFailureWithoutExceptionDetails()
    {
        Exception thrown = CaptureSecretFailure();
        string publicCode = string.Empty;
        string publicMessage = string.Empty;
        int publicReportCount = 0;

        VRCSDKUploadFailureBoundary.ReportFailure(
            thrown,
            _ => { },
            (code, message) =>
            {
                publicReportCount++;
                publicCode = code;
                publicMessage = message;
            });

        Assert.Equal(1, publicReportCount);
        Assert.Equal("VRCSDK_BUILD_FAILED", publicCode);
        Assert.Equal(
            "VRC SDK build or upload failed. See the Unity Console for details.",
            publicMessage);
        Assert.DoesNotContain(nameof(UploadSecretException), publicMessage);
        Assert.DoesNotContain("SECRET_UPLOAD_MESSAGE", publicMessage);
        Assert.DoesNotContain(SecretPath, publicMessage);
    }

    [Fact]
    public void ReportFailure_PassesOriginalExceptionToPrivateSink()
    {
        Exception thrown = CaptureSecretFailure();
        Exception? privateException = null;
        int privateReportCount = 0;

        VRCSDKUploadFailureBoundary.ReportFailure(
            thrown,
            exception =>
            {
                privateReportCount++;
                privateException = exception;
            },
            (_, _) => { });

        Assert.Equal(1, privateReportCount);
        Assert.Same(thrown, privateException);

        string privateText = privateException!.ToString();
        Assert.Contains(nameof(UploadSecretException), privateText);
        Assert.Contains("SECRET_UPLOAD_MESSAGE", privateText);
        Assert.Contains(SecretPath, privateText);
        Assert.Contains(nameof(CaptureSecretFailure), privateText);
    }

    private static Exception CaptureSecretFailure()
    {
        try
        {
            throw new UploadSecretException(
                $"SECRET_UPLOAD_MESSAGE at {SecretPath}");
        }
        catch (Exception exception)
        {
            return exception;
        }
    }

    private sealed class UploadSecretException : Exception
    {
        public UploadSecretException(string message)
            : base(message)
        {
        }
    }
}
