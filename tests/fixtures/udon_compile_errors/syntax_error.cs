// Intentional syntax error fixture for issue #86.
//
// UdonSharp should report this as a compile-time (CS) failure rather
// than letting it through to the Udon assembler.  The missing semicolon
// on the return statement is the canonical trivial reproducer.

using UdonSharp;
using UnityEngine;

public class SyntaxErrorExample : UdonSharpBehaviour
{
    public int Compute()
    {
        int value = 42
        return value;
    }
}
