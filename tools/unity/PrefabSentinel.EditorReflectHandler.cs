using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using UnityEditor;
using UnityEngine;

namespace PrefabSentinel
{
    /// <summary>
    /// Action handler for editor_reflect: search, get_type, get_member.
    /// DTOs use public fields for JsonUtility compatibility.
    /// </summary>
    public static class EditorReflectHandler
    {
        // ── Response DTOs ──

        [Serializable] public sealed class ReflectSearchResult
        {
            public string query = string.Empty;
            public string scope = string.Empty;
            public int count;
            public ReflectSearchEntry[] results = Array.Empty<ReflectSearchEntry>();
            public bool truncated;
        }

        [Serializable] public sealed class ReflectSearchEntry
        {
            public string name = string.Empty;
            public string full_name = string.Empty;
            public string namespace_ = string.Empty;
            public string assembly = string.Empty;
            public bool is_class, is_enum, is_interface, is_struct;
        }

        [Serializable] public sealed class ReflectTypeInfo
        {
            public bool found, ambiguous;
            public string name = string.Empty;
            public string full_name = string.Empty;
            public string namespace_ = string.Empty;
            public string assembly = string.Empty;
            public string base_class = string.Empty;
            public string[] interfaces = Array.Empty<string>();
            public bool is_abstract, is_sealed, is_static, is_enum, is_interface;
            public string[] methods = Array.Empty<string>();
            public string[] properties = Array.Empty<string>();
            public string[] fields = Array.Empty<string>();
            public string[] events = Array.Empty<string>();
            public string[] extension_methods = Array.Empty<string>();
            public string[] obsolete_members = Array.Empty<string>();
        }

        [Serializable] public sealed class ReflectMemberResult
        {
            public bool found;
            public string type_name = string.Empty;
            public string member_name = string.Empty;
            public string member_type = string.Empty;
            public string property_type = string.Empty;
            public bool can_read, can_write;
            public string field_type = string.Empty;
            public bool is_static, is_readonly, is_constant;
            public string constant_value = string.Empty;
            public bool is_obsolete;
            public string obsolete_message = string.Empty;
            public string declaring_type = string.Empty;
            public string event_handler_type = string.Empty;
            public int overload_count;
            public ReflectMethodOverload[] overloads = Array.Empty<ReflectMethodOverload>();
        }

        [Serializable] public sealed class ReflectMethodOverload
        {
            public string signature = string.Empty;
            public string return_type = string.Empty;
            public ReflectParameter[] parameters = Array.Empty<ReflectParameter>();
            public bool is_static, is_virtual, is_abstract, is_generic;
            public string[] generic_arguments = Array.Empty<string>();
            public bool is_obsolete;
            public string obsolete_message = string.Empty;
            public string declaring_type = string.Empty;
        }

        [Serializable] public sealed class ReflectParameter
        {
            public string name = string.Empty;
            public string type_ = string.Empty;
            public bool has_default;
            public string default_value = string.Empty;
            public bool is_params;
        }

        [Serializable] public sealed class ReflectAmbiguousResult
        {
            public bool found;
            public bool ambiguous = true;
            public string query = string.Empty;
            public string[] matches = Array.Empty<string>();
            public string hint = string.Empty;
        }

        // ── Entry Point ──

        public static UnityEditorControlBridge.EditorControlResponse Handle(
            UnityEditorControlBridge.EditorControlRequest request)
        {
            if (EditorApplication.isCompiling)
                return UnityEditorControlBridge.BuildError(
                    "EDITOR_REFLECT_COMPILING",
                    "Cannot reflect while Unity is compiling. Wait for domain reload to complete.");

            switch (request.reflect_action)
            {
                case "search": return HandleSearch(request);
                case "get_type": return HandleGetType(request);
                case "get_member": return HandleGetMember(request);
                default:
                    return UnityEditorControlBridge.BuildError(
                        "EDITOR_REFLECT_UNKNOWN_ACTION",
                        $"Unknown reflect action: '{request.reflect_action}'. Supported: search, get_type, get_member.");
            }
        }

        // ── Action Handlers ──

        private static UnityEditorControlBridge.EditorControlResponse HandleSearch(
            UnityEditorControlBridge.EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.query))
                return UnityEditorControlBridge.BuildError(
                    "EDITOR_REFLECT_MISSING_PARAM", "'query' is required for search.");

            string scope = string.IsNullOrEmpty(request.scope) ? "all" : request.scope;
            if (scope != "unity" && scope != "packages" && scope != "project" && scope != "all")
                return UnityEditorControlBridge.BuildError(
                    "EDITOR_REFLECT_INVALID_SCOPE",
                    $"Invalid scope: '{scope}'. Supported: unity, packages, project, all.");

            string queryLower = request.query.ToLowerInvariant();
            var candidates = new List<(Type type, int rank)>();

            foreach (var kvp in ReflectTypeResolver.GetAssemblyTypeCache())
            {
                var types = kvp.Value;
                if (types.Length == 0) continue;
                if (!ReflectTypeResolver.MatchesScope(types[0].Assembly.GetName().Name, scope))
                    continue;
                foreach (var t in types)
                {
                    if (t.Name == null) continue;
                    string nameLower = t.Name.ToLowerInvariant();
                    string fullNameLower = t.FullName?.ToLowerInvariant() ?? nameLower;
                    if (nameLower == queryLower || fullNameLower == queryLower) candidates.Add((t, 0));
                    else if (nameLower.StartsWith(queryLower)) candidates.Add((t, 1));
                    else if (nameLower.Contains(queryLower) || fullNameLower.Contains(queryLower)) candidates.Add((t, 2));
                }
            }

            var sorted = candidates.OrderBy(c => c.rank).ThenBy(c => c.type.FullName).ToArray();
            var top = sorted.Take(25).Select(c => new ReflectSearchEntry
            {
                name = c.type.Name,
                full_name = c.type.FullName ?? c.type.Name,
                namespace_ = c.type.Namespace ?? string.Empty,
                assembly = c.type.Assembly.GetName().Name,
                is_class = c.type.IsClass, is_enum = c.type.IsEnum,
                is_interface = c.type.IsInterface,
                is_struct = c.type.IsValueType && !c.type.IsEnum
            }).ToArray();

            var dto = new ReflectSearchResult
            {
                query = request.query, scope = scope, count = top.Length,
                truncated = sorted.Length > 25, results = top
            };
            return BuildReflectSuccess("EDITOR_REFLECT_SEARCH",
                $"Found {top.Length} type(s) matching '{request.query}' (scope: {scope}).", dto);
        }

        private static UnityEditorControlBridge.EditorControlResponse HandleGetType(
            UnityEditorControlBridge.EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.class_name))
                return UnityEditorControlBridge.BuildError(
                    "EDITOR_REFLECT_MISSING_PARAM", "'class_name' is required for get_type.");

            string normalized = ReflectTypeResolver.NormalizeGenericName(request.class_name);
            var ambiguity = CheckAmbiguity(normalized, request.class_name);
            if (ambiguity != null) return ambiguity;

            var type = ReflectTypeResolver.ResolveType(normalized);
            if (type == null) return BuildNotFound(request.class_name);

            var flags = BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly;
            var dto = new ReflectTypeInfo
            {
                found = true,
                name = ReflectTypeResolver.FormatTypeName(type),
                full_name = type.FullName ?? type.Name,
                namespace_ = type.Namespace ?? string.Empty,
                assembly = type.Assembly.GetName().Name,
                base_class = type.BaseType != null ? ReflectTypeResolver.FormatTypeName(type.BaseType) : string.Empty,
                interfaces = type.GetInterfaces().Select(i => ReflectTypeResolver.FormatTypeName(i)).OrderBy(n => n).ToArray(),
                is_abstract = type.IsAbstract, is_sealed = type.IsSealed,
                is_static = type.IsAbstract && type.IsSealed,
                is_enum = type.IsEnum, is_interface = type.IsInterface,
                methods = type.GetMethods(flags).Where(m => !m.IsSpecialName).Select(m => m.Name).Distinct().OrderBy(n => n).ToArray(),
                properties = type.GetProperties(flags).Select(p => p.Name).Distinct().OrderBy(n => n).ToArray(),
                fields = type.GetFields(flags).Select(f => f.Name).Distinct().OrderBy(n => n).ToArray(),
                events = type.GetEvents(flags).Select(e => e.Name).Distinct().OrderBy(n => n).ToArray(),
                extension_methods = ReflectTypeResolver.FindExtensionMethodNames(type),
                obsolete_members = ReflectTypeResolver.GetObsoleteMembers(type, flags)
            };
            return BuildReflectSuccess("EDITOR_REFLECT_GET_TYPE", $"Type info for '{dto.name}'.", dto);
        }

        private static UnityEditorControlBridge.EditorControlResponse HandleGetMember(
            UnityEditorControlBridge.EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.class_name))
                return UnityEditorControlBridge.BuildError(
                    "EDITOR_REFLECT_MISSING_PARAM", "'class_name' is required for get_member.");
            if (string.IsNullOrEmpty(request.member_name))
                return UnityEditorControlBridge.BuildError(
                    "EDITOR_REFLECT_MISSING_PARAM", "'member_name' is required for get_member.");

            string normalized = ReflectTypeResolver.NormalizeGenericName(request.class_name);
            var ambiguity = CheckAmbiguity(normalized, request.class_name);
            if (ambiguity != null) return ambiguity;

            var type = ReflectTypeResolver.ResolveType(normalized);
            if (type == null) return BuildNotFound(request.class_name);

            string member = request.member_name;
            var flags = BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static;
            string typeName = ReflectTypeResolver.FormatTypeName(type);

            var methods = type.GetMethods(flags).Where(m => !m.IsSpecialName && m.Name == member).ToArray();
            if (methods.Length > 0) return BuildMethodResult(typeName, member, methods, "method");

            var prop = type.GetProperty(member, flags);
            if (prop != null) return BuildPropertyResult(typeName, member, prop, type);

            var field = type.GetField(member, flags);
            if (field != null) return BuildFieldResult(typeName, member, field, type);

            var evt = type.GetEvent(member, flags);
            if (evt != null) return BuildEventResult(typeName, member, evt, type);

            var ext = ReflectTypeResolver.FindExtensionMethodInfos(type, member);
            if (ext.Length > 0)
                return BuildMethodResult(typeName, member, ext, "extension_method",
                    ReflectTypeResolver.FormatTypeName(ext[0].DeclaringType));

            return BuildReflectSuccess("EDITOR_REFLECT_GET_MEMBER",
                $"Member '{member}' not found on '{typeName}'.",
                new ReflectMemberResult { type_name = typeName, member_name = member });
        }

        // ── Shared Helpers ──

        private static UnityEditorControlBridge.EditorControlResponse CheckAmbiguity(
            string normalized, string original)
        {
            if (normalized.Contains('.') || normalized.Contains('`')) return null;
            var matches = ReflectTypeResolver.FindAllTypesByShortName(normalized);
            if (matches.Count <= 1) return null;
            return BuildReflectSuccess("EDITOR_REFLECT_AMBIGUOUS",
                $"Ambiguous type name '{original}'.",
                new ReflectAmbiguousResult
                {
                    found = true, query = original,
                    matches = matches.Select(t => t.FullName).OrderBy(n => n).ToArray(),
                    hint = "Use the fully qualified name (e.g., 'UnityEngine.UI.Button') to disambiguate."
                });
        }

        private static UnityEditorControlBridge.EditorControlResponse BuildNotFound(string className)
        {
            return BuildReflectSuccess("EDITOR_REFLECT_NOT_FOUND",
                $"Type '{className}' not found.",
                new ReflectTypeInfo { name = className });
        }

        private static ReflectParameter BuildParam(ParameterInfo p)
        {
            return new ReflectParameter
            {
                name = p.Name ?? string.Empty,
                type_ = ReflectTypeResolver.FormatParamPrefix(p) + ReflectTypeResolver.FormatTypeName(p.ParameterType),
                has_default = p.HasDefaultValue,
                default_value = p.HasDefaultValue ? ReflectTypeResolver.FormatDefaultValue(p.DefaultValue) : string.Empty,
                is_params = p.IsDefined(typeof(ParamArrayAttribute))
            };
        }

        private static ReflectMethodOverload BuildOverload(MethodInfo m)
        {
            var obsolete = m.GetCustomAttribute<ObsoleteAttribute>();
            return new ReflectMethodOverload
            {
                signature = ReflectTypeResolver.FormatMethodSignature(m),
                return_type = ReflectTypeResolver.FormatTypeName(m.ReturnType),
                parameters = m.GetParameters().Select(BuildParam).ToArray(),
                is_static = m.IsStatic, is_virtual = m.IsVirtual && !m.IsFinal,
                is_abstract = m.IsAbstract, is_generic = m.IsGenericMethod,
                generic_arguments = m.IsGenericMethod
                    ? m.GetGenericArguments().Select(a => a.Name).ToArray() : Array.Empty<string>(),
                is_obsolete = obsolete != null,
                obsolete_message = obsolete?.Message ?? string.Empty,
                declaring_type = m.DeclaringType != m.ReflectedType
                    ? ReflectTypeResolver.FormatTypeName(m.DeclaringType) : string.Empty
            };
        }

        private static UnityEditorControlBridge.EditorControlResponse BuildMethodResult(
            string typeName, string memberName, MethodInfo[] methods, string memberType,
            string declaringOverride = "")
        {
            var overloads = methods.Select(BuildOverload).ToArray();
            return BuildReflectSuccess("EDITOR_REFLECT_GET_MEMBER",
                $"Member '{memberName}' on '{typeName}'.",
                new ReflectMemberResult
                {
                    found = true, type_name = typeName, member_name = memberName,
                    member_type = memberType, overload_count = overloads.Length,
                    overloads = overloads, declaring_type = declaringOverride
                });
        }

        private static UnityEditorControlBridge.EditorControlResponse BuildPropertyResult(
            string typeName, string memberName, PropertyInfo prop, Type ownerType)
        {
            var obs = prop.GetCustomAttribute<ObsoleteAttribute>();
            return BuildReflectSuccess("EDITOR_REFLECT_GET_MEMBER",
                $"Member '{memberName}' on '{typeName}'.",
                new ReflectMemberResult
                {
                    found = true, type_name = typeName, member_name = memberName,
                    member_type = "property",
                    property_type = ReflectTypeResolver.FormatTypeName(prop.PropertyType),
                    can_read = prop.CanRead, can_write = prop.CanWrite,
                    is_static = (prop.GetMethod ?? prop.SetMethod)?.IsStatic ?? false,
                    is_obsolete = obs != null, obsolete_message = obs?.Message ?? string.Empty,
                    declaring_type = prop.DeclaringType != ownerType
                        ? ReflectTypeResolver.FormatTypeName(prop.DeclaringType) : string.Empty
                });
        }

        private static UnityEditorControlBridge.EditorControlResponse BuildFieldResult(
            string typeName, string memberName, FieldInfo field, Type ownerType)
        {
            var obs = field.GetCustomAttribute<ObsoleteAttribute>();
            return BuildReflectSuccess("EDITOR_REFLECT_GET_MEMBER",
                $"Member '{memberName}' on '{typeName}'.",
                new ReflectMemberResult
                {
                    found = true, type_name = typeName, member_name = memberName,
                    member_type = "field",
                    field_type = ReflectTypeResolver.FormatTypeName(field.FieldType),
                    is_static = field.IsStatic, is_readonly = field.IsInitOnly,
                    is_constant = field.IsLiteral,
                    constant_value = field.IsLiteral ? (field.GetRawConstantValue()?.ToString() ?? "null") : string.Empty,
                    is_obsolete = obs != null, obsolete_message = obs?.Message ?? string.Empty,
                    declaring_type = field.DeclaringType != ownerType
                        ? ReflectTypeResolver.FormatTypeName(field.DeclaringType) : string.Empty
                });
        }

        private static UnityEditorControlBridge.EditorControlResponse BuildEventResult(
            string typeName, string memberName, EventInfo evt, Type ownerType)
        {
            var obs = evt.GetCustomAttribute<ObsoleteAttribute>();
            return BuildReflectSuccess("EDITOR_REFLECT_GET_MEMBER",
                $"Member '{memberName}' on '{typeName}'.",
                new ReflectMemberResult
                {
                    found = true, type_name = typeName, member_name = memberName,
                    member_type = "event",
                    event_handler_type = ReflectTypeResolver.FormatTypeName(evt.EventHandlerType),
                    is_static = evt.AddMethod?.IsStatic ?? false,
                    is_obsolete = obs != null, obsolete_message = obs?.Message ?? string.Empty,
                    declaring_type = evt.DeclaringType != ownerType
                        ? ReflectTypeResolver.FormatTypeName(evt.DeclaringType) : string.Empty
                });
        }

        private static UnityEditorControlBridge.EditorControlResponse BuildReflectSuccess<T>(
            string code, string message, T dto)
        {
            var data = new UnityEditorControlBridge.EditorControlData
            {
                executed = true,
                reflect_result_json = JsonUtility.ToJson(dto)
            };
            return UnityEditorControlBridge.BuildSuccess(code, message, data);
        }
    }
}
