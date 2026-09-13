using System;

using Xunit;

namespace PrefabSentinel.Tests;

public class EditorBridgeTerminalFailureBoundaryTests
{
    private const string SecretPath = @"C:\private\SECRET_PATH\request.json";

    [Fact]
    public void ReportFailure_UsesFixedPublicMessageWithoutExceptionDetails()
    {
        Exception thrown = CaptureTerminalSecret();
        string publicMessage = string.Empty;
        int publicReportCount = 0;

        EditorBridgeTerminalFailureBoundary.ReportFailure(
            thrown,
            _ => { },
            message =>
            {
                publicReportCount++;
                publicMessage = message;
            });

        Assert.Equal(1, publicReportCount);
        Assert.Equal("Editor Bridge request processing failed.", publicMessage);
        Assert.DoesNotContain(nameof(TerminalSecretException), publicMessage);
        Assert.DoesNotContain("SECRET_MESSAGE", publicMessage);
        Assert.DoesNotContain(SecretPath, publicMessage);
    }

    [Fact]
    public void ReportFailure_PassesOriginalExceptionToPrivateSink()
    {
        Exception thrown = CaptureTerminalSecret();
        Exception? privateException = null;
        int privateReportCount = 0;

        EditorBridgeTerminalFailureBoundary.ReportFailure(
            thrown,
            exception =>
            {
                privateReportCount++;
                privateException = exception;
            },
            _ => { });

        Assert.Equal(1, privateReportCount);
        Assert.Same(thrown, privateException);

        string privateText = privateException!.ToString();
        Assert.Contains(nameof(TerminalSecretException), privateText);
        Assert.Contains("SECRET_MESSAGE", privateText);
        Assert.Contains(SecretPath, privateText);
        Assert.Contains(nameof(CaptureTerminalSecret), privateText);
    }

    private static Exception CaptureTerminalSecret()
    {
        try
        {
            throw new TerminalSecretException("SECRET_MESSAGE " + SecretPath);
        }
        catch (Exception exception)
        {
            return exception;
        }
    }

    private sealed class TerminalSecretException : Exception
    {
        public TerminalSecretException(string message) : base(message)
        {
        }
    }
}
