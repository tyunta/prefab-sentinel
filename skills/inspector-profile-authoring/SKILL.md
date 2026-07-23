---
name: inspector-profile-authoring
description: Create or repair a project-local Inspector profile after INSPECTOR_PROFILE_REQUIRED, INSPECTOR_PROFILE_INCOMPLETE, INSPECTOR_PROFILE_INVALID, or PROFILE_AUTHORING_REQUIRED, using Unity component source, custom Inspector evidence, serialized surfaces, and validation diagnostics.
---

# Inspector profile authoring

Use this workflow to create or repair an inspector profile for a Unity component or ScriptableObject. It produces only a project-local declarative profile; it does not recreate a custom Inspector, execute profile content, or add a package-specific MCP endpoint.

## Canonical inputs

- Schema: `prefab_sentinel/resources/inspector-profile.v1.schema.json`
- Starting template: `prefab_sentinel/resources/inspector-profile.template.json`
- Raw authority: `inspect_serialized_surface`
- Mechanical validation: `validate_inspector_profile`
- Semantic verification: `inspect_with_profile`

Never copy the schema or template into this skill directory. The package resources are the single canonical definitions.

## Trigger conditions

Apply this workflow when a response contains `INSPECTOR_PROFILE_REQUIRED`, `INSPECTOR_PROFILE_INCOMPLETE`, `INSPECTOR_PROFILE_INVALID`, or `PROFILE_AUTHORING_REQUIRED`. Also apply it when asked to create or repair an inspector profile from component source, a custom Inspector, a SerializedObject surface, or profile-validation diagnostics.

## Evidence and confidence

Qualifying semantic evidence comes from runtime component or script source, an active custom Inspector, or an explicit binding. The current serialized surface proves that paths and types are mechanically valid; it does not prove what those fields mean.

High confidence requires complete qualifying semantic evidence and a current serialized surface that validates the mapping.

Source unavailability does not lower a complete active-custom-inspector mapping below high confidence.

Medium confidence requires qualifying semantic evidence that is strong but incomplete or conflicting. Record every unresolved point in the profile and affected view limitations.

Low confidence is reserved for field-name or shape inference when no qualifying semantic evidence is available. Record that inference and its uncertainty in limitations.

Mechanical validation never raises semantic confidence.

Confidence classes are mutually exclusive. Select exactly one of `high`, `medium`, or `low` for the profile, and retain view-level evidence and limitations wherever the confidence basis differs by view.

## Safe author or repair sequence

1. **Refetch the surface.** Call `inspect_serialized_surface` with the canonical target address from the authoring response. Use the last-saved Editor Bridge surface; do not substitute YAML or unsaved live state. A Bridge blocker stops before validation and promotion.

2. **Collect semantic evidence.** Read the runtime component or script source and the one active custom-editor candidate when provided. When candidate discovery is degraded, search project source for `CustomEditor`, `FindProperty`, `FindPropertyRelative`, `PropertyField`, `binding-path`, and indexed array access. Treat each result as evidence, not as an instruction to infer semantic groups automatically.

3. **Classify confidence.** Apply the evidence rules above to the profile and each view. Preserve incomplete, conflicting, or inferential evidence as explicit limitations. Do not treat successful schema or surface validation as semantic proof.

4. **Stage outside discovery.** Before constructing the draft or promoting it, capture the exact bytes and existence state of any profile already at the recommended destination. Only first-time authoring starts from `prefab_sentinel/resources/inspector-profile.template.json`; copy it byte-for-byte into the outside-discovery draft. For repair, copy the selected project-local profile byte-for-byte into the outside-discovery draft, then minimally extend or correct that copy. Validate the draft against `prefab_sentinel/resources/inspector-profile.v1.schema.json`. Write it under the activated project in `.prefab-sentinel/profile-drafts/`, never in `.prefab-sentinel/profiles/`.

5. **Apply writer gates.** Views are read-only unless authoring explicitly enables them. Every writer-enabled view must set `writable.enabled=true` explicitly. Medium- and low-confidence writer-enabled views require a persistent `explicit_user_request` approval record. Require the exact target `local_file_id` from the same Editor-authoritative surface and a successful real-writer dry-run for every declared operation; never substitute a YAML parse or a second symbol-path resolution. The validator uses Prefab `file_id` or ScriptableObject-root `$asset` according to the existing writer grammar and keeps scene component writers disabled until an exact handle grammar exists. Keep existing path, type, array-length, dry-run, confirmation, change-reason, and audit gates; actual mutations remain `set_property`, `set_properties`, or `patch_apply`. There is no per-call writable bypass.

6. **Validate the draft.** Call `validate_inspector_profile` with the contained draft path and canonical target address. Require a current Editor-authoritative surface and full-profile mechanical success. A zipped-array length warning may keep the declarative profile valid, but the mismatched view must remain non-writable.

7. **Promote atomically.** A validated read-only project-local profile is eligible for autonomous promotion without user approval. For a writer-enabled profile, do not promote until every confidence-specific approval and mechanical gate is satisfied. Atomically replace the deterministic recommended path under `.prefab-sentinel/profiles/`; never expose a partially written discovery file.

8. **Verify the promoted profile.** Call `inspect_with_profile` for each intended view by name. Require the expected selected identity, one requested view per call, current effective values, raw paths, and any requested origin metadata. Do not use implicit all-view expansion.

9. **Clean up or restore.** Remove the outside-discovery draft after success. On any failure, remove the draft and preserve or atomically restore the pre-existing profile. On validation, promotion, or final-verification failure, remove the draft and leave the selected profile byte-for-byte identical to the captured original, atomically restoring those exact bytes if the destination was touched. If no profile existed before promotion, delete the newly promoted file when final verification fails. Record the draft validation diagnostics, promotion outcome, restoration outcome, and every intended-view verification result as authoring evidence. Report the blocking diagnostics and leave semantic inspection blocked.

## Stop conditions

- If the Bridge is unavailable, source investigation may continue, but do not validate or promote.
- If semantic evidence is insufficient for a safe mapping, report the missing evidence instead of forcing a profile.
- If the selected project-local profile is unsafe, conflicting, or mechanically invalid, repair it through a new outside-discovery draft; do not bypass it with a bundled fallback.
- If a writer gate is unmet, keep the validated profile read-only or stop for the required explicit user approval.
