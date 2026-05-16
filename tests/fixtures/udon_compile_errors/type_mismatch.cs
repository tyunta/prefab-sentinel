// Intentional type-mismatch fixture for issue #86.
//
// Assigning a string literal to an int field is a hard compile error
// (CS0029) that UdonSharp should surface with a file + line annotation.

using UdonSharp;
using UnityEngine;

public class TypeMismatchExample : UdonSharpBehaviour
{
    public int Count;

    public void Start()
    {
        Count = "not an int";
    }
}
