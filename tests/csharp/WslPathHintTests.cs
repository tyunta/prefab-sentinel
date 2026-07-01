using Xunit;

namespace PrefabSentinel.Tests;

public class WslPathHintTests
{
    [Fact]
    public void Mounted_Drive_Path_Produces_Windows_Asset_And_Data_Path_Guidance()
    {
        WslPathHint[] hints = WslPathHintDetector.FindHints(
            "var p = \"/mnt/c/Users/me/World/Assets/Scenes/Main.unity\";");

        Assert.Single(hints);
        Assert.Equal((
            "/mnt/c/Users/me/World/Assets/Scenes/Main.unity",
            "C:\\Users\\me\\World\\Assets\\Scenes\\Main.unity",
            "Assets/Scenes/Main.unity",
            "Application.dataPath + \"/Scenes/Main.unity\""), (
            hints[0].DetectedPath,
            hints[0].WindowsPath,
            hints[0].AssetRelativePath,
            hints[0].ApplicationDataPath));
    }

    [Fact]
    public void Runtime_Exception_Text_Is_Scanned_For_Mounted_Drive_Paths()
    {
        WslPathHint[] hints = WslPathHintDetector.FindHints(
            "throw new Exception();",
            "FileNotFoundException: /mnt/d/project/Assets/Data/config.json");

        Assert.Single(hints);
        Assert.Equal("D:\\project\\Assets\\Data\\config.json", hints[0].WindowsPath);
        Assert.Equal("Assets/Data/config.json", hints[0].AssetRelativePath);
    }

    [Fact]
    public void Non_Wsl_Drive_Paths_Are_Ignored()
    {
        WslPathHint[] hints = WslPathHintDetector.FindHints(
            "var a = \"C:\\\\project\\\\Assets\\\\Scene.unity\"; var b = \"/tmp/file\";");

        Assert.Empty(hints);
    }

    [Fact]
    public void Redaction_Covers_Linux_Opt_Usr_And_Unc_Absolute_Paths()
    {
        string redacted = WslPathHintDetector.RedactAbsolutePaths(
            @"open /usr/local/bin/tool then /opt/unity/Editor and \\host\share\project\Assets\World.unity");

        Assert.Equal(
            "open <absolute-path> then <absolute-path> and <absolute-path>",
            redacted);
    }

    [Fact]
    public void Redaction_Covers_Spaced_Windows_And_Wsl_Mounted_Paths()
    {
        string windowsRedacted = WslPathHintDetector.RedactAbsolutePaths(
            @"open C:\Program Files\Unity\Editor\Data\Tools.exe");
        string wslRedacted = WslPathHintDetector.RedactAbsolutePaths(
            @"open /mnt/c/Program Files/Unity/Editor/Data/Tools.exe");

        Assert.Equal("open <absolute-path>", windowsRedacted);
        Assert.Equal("open <wsl-path>", wslRedacted);
        Assert.DoesNotContain("Program", windowsRedacted + wslRedacted);
        Assert.DoesNotContain("Files", windowsRedacted + wslRedacted);
    }

    [Fact]
    public void Quoted_Spaced_Paths_Do_Not_Leak_Path_Fragments()
    {
        string redacted = WslPathHintDetector.RedactAbsolutePaths(
            @"paths ""C:\Program Files\Unity\Editor\Data\Tools.exe"" and ""/mnt/c/Program Files/Unity/Editor/Data/Tools.exe""");

        Assert.Equal("paths \"<absolute-path>\" and \"<wsl-path>\"", redacted);
        Assert.DoesNotContain("Unity", redacted);
    }

    [Fact]
    public void Exception_Summary_Redacts_Spaced_Path_Message()
    {
        RunScriptExceptionSummary summary = RunScriptExceptionSummary.FromException(
            new InvalidOperationException(@"C:\Program Files\Unity\Editor\Data\Tools.exe"));

        Assert.Equal("<absolute-path>", summary.message);
    }
}
