using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Text.RegularExpressions;
using UnityEditor;

namespace PrefabSentinel
{
    /// <summary>
    /// Type resolution infrastructure, formatting, and caching for editor_reflect.
    /// </summary>
    internal static class ReflectTypeResolver
    {
        private static Dictionary<string, Type[]> _assemblyTypeCache;
        private static readonly object CacheLock = new();
        private static Dictionary<Type, string[]> _extensionMethodCache = new();

        private static readonly string[] NamespacePrefixes =
        {
            "UnityEngine.", "UnityEditor.", "UnityEngine.UI.",
            "Unity.Cinemachine.", "UnityEngine.AI.",
            "UnityEngine.Rendering.Universal.", "UnityEngine.Rendering.HighDefinition.",
            "UnityEngine.InputSystem.", "UnityEngine.ProBuilder.",
            "UnityEngine.Tilemaps.", "UnityEngine.EventSystems.",
            "UnityEngine.Rendering.", "UnityEngine.SceneManagement.",
            "UnityEngine.Animations.", "UnityEngine.Playables.",
            "UnityEngine.UIElements.",
            "VRC.SDK3.Avatars.Components.", "VRC.SDKBase.", "VRC.Udon.", "TMPro."
        };

        private static readonly Dictionary<Type, string> FriendlyTypeNames = new()
        {
            { typeof(void), "void" }, { typeof(int), "int" }, { typeof(float), "float" },
            { typeof(bool), "bool" }, { typeof(string), "string" }, { typeof(double), "double" },
            { typeof(long), "long" }, { typeof(object), "object" }, { typeof(byte), "byte" },
            { typeof(short), "short" }, { typeof(char), "char" }, { typeof(decimal), "decimal" },
            { typeof(uint), "uint" }, { typeof(ulong), "ulong" },
            { typeof(ushort), "ushort" }, { typeof(sbyte), "sbyte" }
        };

        [InitializeOnLoadMethod]
        private static void OnLoad()
        {
            AssemblyReloadEvents.afterAssemblyReload += InvalidateCache;
        }

        private static void InvalidateCache()
        {
            lock (CacheLock) { _assemblyTypeCache = null; }
            _extensionMethodCache = new Dictionary<Type, string[]>();
        }

        internal static Dictionary<string, Type[]> GetAssemblyTypeCache()
        {
            lock (CacheLock)
            {
                if (_assemblyTypeCache != null)
                    return _assemblyTypeCache;

                _assemblyTypeCache = new Dictionary<string, Type[]>();
                foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
                {
                    try { _assemblyTypeCache[asm.FullName] = asm.GetExportedTypes(); }
                    catch (Exception) { /* Dynamic/transient assemblies throw — expected and harmless */ }
                }
                return _assemblyTypeCache;
            }
        }

        internal static Type ResolveType(string normalizedName)
        {
            var type = Type.GetType(normalizedName);
            if (type != null) return type;

            var cache = GetAssemblyTypeCache();
            foreach (var prefix in NamespacePrefixes)
            {
                string fullName = prefix + normalizedName;
                type = Type.GetType(fullName);
                if (type != null) return type;
                foreach (var kvp in cache)
                {
                    type = Array.Find(kvp.Value, t => t.FullName == fullName);
                    if (type != null) return type;
                }
            }
            foreach (var kvp in cache)
            {
                type = Array.Find(kvp.Value, t => t.FullName == normalizedName || t.Name == normalizedName);
                if (type != null) return type;
            }
            return null;
        }

        internal static List<Type> FindAllTypesByShortName(string shortName)
        {
            var matches = new List<Type>();
            foreach (var kvp in GetAssemblyTypeCache())
                foreach (var t in kvp.Value)
                    if (t.Name == shortName) matches.Add(t);
            return matches;
        }

        internal static string NormalizeGenericName(string name)
        {
            var match = Regex.Match(name, @"^(.+)<(.+)>$");
            if (!match.Success) return name;

            int argCount = 1, depth = 0;
            foreach (char c in match.Groups[2].Value)
            {
                if (c == '<') depth++;
                else if (c == '>') depth--;
                else if (c == ',' && depth == 0) argCount++;
            }
            return $"{match.Groups[1].Value}`{argCount}";
        }

        internal static bool MatchesScope(string assemblyName, string scope)
        {
            switch (scope)
            {
                case "unity":
                    return assemblyName.StartsWith("UnityEngine")
                        || assemblyName.StartsWith("UnityEditor")
                        || assemblyName.StartsWith("Unity.");
                case "packages":
                    return !assemblyName.StartsWith("System")
                        && !assemblyName.StartsWith("mscorlib")
                        && !assemblyName.StartsWith("netstandard");
                case "project":
                    return assemblyName == "Assembly-CSharp"
                        || assemblyName == "Assembly-CSharp-Editor"
                        || assemblyName.StartsWith("Assembly-CSharp-firstpass")
                        || assemblyName.StartsWith("Assembly-CSharp-Editor-firstpass");
                case "all": return true;
                default: return false;
            }
        }

        internal static string[] FindExtensionMethodNames(Type targetType)
        {
            if (_extensionMethodCache.TryGetValue(targetType, out var cached))
                return cached;
            var names = new HashSet<string>();
            foreach (var m in EnumerateExtensionMethods(targetType, null))
                names.Add(m.Name);
            var result = names.Count > 0 ? names.OrderBy(n => n).ToArray() : Array.Empty<string>();
            _extensionMethodCache[targetType] = result;
            return result;
        }

        internal static MethodInfo[] FindExtensionMethodInfos(Type targetType, string methodName)
        {
            return EnumerateExtensionMethods(targetType, methodName).ToArray();
        }

        private static IEnumerable<MethodInfo> EnumerateExtensionMethods(Type targetType, string nameFilter)
        {
            foreach (var kvp in GetAssemblyTypeCache())
            {
                string asmName = kvp.Key.Split(',')[0];
                if (!asmName.StartsWith("UnityEngine") && !asmName.StartsWith("UnityEditor") && !asmName.StartsWith("Unity."))
                    continue;
                foreach (var t in kvp.Value)
                {
                    if (!t.IsAbstract || !t.IsSealed) continue;
                    if (!t.IsDefined(typeof(ExtensionAttribute), false)) continue;
                    foreach (var method in t.GetMethods(BindingFlags.Public | BindingFlags.Static))
                    {
                        if (nameFilter != null && method.Name != nameFilter) continue;
                        if (!method.IsDefined(typeof(ExtensionAttribute), false)) continue;
                        var firstParam = method.GetParameters().FirstOrDefault();
                        if (firstParam == null) continue;
                        if (IsExtensionApplicable(firstParam.ParameterType, targetType))
                            yield return method;
                    }
                }
            }
        }

        internal static string FormatTypeName(Type type)
        {
            if (type == null) return "null";
            if (FriendlyTypeNames.TryGetValue(type, out var friendly)) return friendly;
            if (type.IsArray) return FormatTypeName(type.GetElementType()) + "[]";
            if (type.IsByRef) return FormatTypeName(type.GetElementType());
            if (type.IsGenericType)
            {
                string baseName = type.Name;
                int idx = baseName.IndexOf('`');
                if (idx > 0) baseName = baseName.Substring(0, idx);
                return $"{baseName}<{string.Join(", ", type.GetGenericArguments().Select(FormatTypeName))}>";
            }
            if (type.IsNested && type.DeclaringType != null)
                return FormatTypeName(type.DeclaringType) + "." + type.Name;
            return type.Name;
        }

        internal static string[] GetObsoleteMembers(Type type, BindingFlags flags)
        {
            var obsolete = new HashSet<string>();
            foreach (var m in type.GetMethods(flags).Where(m => !m.IsSpecialName))
                if (m.GetCustomAttribute<ObsoleteAttribute>() != null) obsolete.Add(m.Name);
            foreach (var p in type.GetProperties(flags))
                if (p.GetCustomAttribute<ObsoleteAttribute>() != null) obsolete.Add(p.Name);
            foreach (var f in type.GetFields(flags))
                if (f.GetCustomAttribute<ObsoleteAttribute>() != null) obsolete.Add(f.Name);
            foreach (var e in type.GetEvents(flags))
                if (e.GetCustomAttribute<ObsoleteAttribute>() != null) obsolete.Add(e.Name);
            return obsolete.Count > 0 ? obsolete.OrderBy(n => n).ToArray() : Array.Empty<string>();
        }

        internal static string FormatMethodSignature(MethodInfo m)
        {
            string prefix = m.IsStatic ? "static " : "";
            string name = m.Name;
            if (m.IsGenericMethod)
                name += "<" + string.Join(", ", m.GetGenericArguments().Select(a => a.Name)) + ">";
            var parms = m.GetParameters().Select(p => FormatParamPrefix(p) + FormatTypeName(p.ParameterType) + " " + p.Name);
            return $"{prefix}{FormatTypeName(m.ReturnType)} {name}({string.Join(", ", parms)})";
        }

        internal static string FormatDefaultValue(object value)
        {
            if (value == null) return "null";
            if (value is string s) return $"\"{s}\"";
            if (value is bool b) return b ? "true" : "false";
            return value.ToString();
        }

        internal static string FormatParamPrefix(ParameterInfo p)
        {
            if (p.IsDefined(typeof(ParamArrayAttribute))) return "params ";
            if (p.IsOut) return "out ";
            if (p.ParameterType.IsByRef) return "ref ";
            return "";
        }

        private static bool IsExtensionApplicable(Type paramType, Type targetType)
        {
            return paramType.IsAssignableFrom(targetType)
                || (paramType.IsGenericType && IsGenericMatch(paramType, targetType));
        }

        private static bool IsGenericMatch(Type genericParamType, Type targetType)
        {
            if (!genericParamType.IsGenericType) return false;
            var genDef = genericParamType.GetGenericTypeDefinition();
            if (targetType.IsGenericType && targetType.GetGenericTypeDefinition() == genDef) return true;
            return targetType.GetInterfaces().Any(i => i.IsGenericType && i.GetGenericTypeDefinition() == genDef);
        }
    }
}
