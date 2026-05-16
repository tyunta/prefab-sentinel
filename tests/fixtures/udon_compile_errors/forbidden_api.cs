// Intentional forbidden-API fixture for issue #86.
//
// UdonSharp restricts the .NET surface exposed to VRChat Udon.  Calling
// ``System.IO.File.ReadAllText`` is rejected by the UdonSharp exposure
// table and should produce a dedicated "API not exposed" diagnostic.

using System.IO;
using UdonSharp;
using UnityEngine;

public class ForbiddenApiExample : UdonSharpBehaviour
{
    public string ReadFromDisk()
    {
        // File.ReadAllText is not in UdonSharp's exposed API surface.
        return File.ReadAllText("Assets/forbidden.txt");
    }
}
