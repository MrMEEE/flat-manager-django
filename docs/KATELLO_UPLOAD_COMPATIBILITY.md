# Katello Upload Checksum Compatibility (Satellite 6.14 vs 6.18)

## Summary

Some Satellite/Katello versions fail RPM imports with:

- `The sha256 checksum did not match.`

This is strongly tied to an upstream Katello behavior difference in how
`content_uploads` request bodies are passed to Pulp.

## Root Cause Found Upstream

In Katello 4.14, `ContentUploadsController#update` passes `params[:content]`
directly to `upload_chunk`.

In Katello 4.20, Katello first reads uploaded-file data before calling
`upload_chunk`:

- `content = params[:content].respond_to?(:read) ? params[:content].read : params[:content]`

Upstream commit introducing this fix:

- `593de75261341744e055336a165e5b06e36cba1c`
- Message: `Fixes #38482 - properly read data of ActionDispatch::Http::UploadedFile`

This explains why older versions can store incorrect upload content and then
fail checksum validation during `import_uploads`.

## Affected Katello Versions

Based on upstream tags containing commit `593de75261341744e055336a165e5b06e36cba1c`:

- First fixed tag: `4.18.0`

### Versions likely affected

- `4.14.x`
- `4.15.x`
- `4.16.x`
- `4.17.x`

### Versions likely not affected

- `4.18.0+`

## Satellite Mapping In Scope

- Satellite 6.14 (Foreman 3.12 / Katello 4.14): likely affected
- Satellite 6.18 (Foreman 3.18 / Katello 4.20): likely not affected

Note: downstream Satellite builds can backport patches, so server-specific
behavior should still be validated in logs.

## Recommended Compatibility Strategy

Use version-aware upload modes.

1. Detect Katello version on the target server.
2. For Katello `< 4.18`:
   - Prefer multipart upload mode that avoids passing `content` as an uploaded
     file object when possible.
   - Avoid relying on raw `application/octet-stream` fallback if that server
     returns `undefined method '+' for nil:NilClass`.
3. For Katello `>= 4.18`:
   - Standard multipart file upload flow is acceptable.
4. Keep checksum-based import validation and explicit logging of:
   - upload mode used
   - upload id
   - import error payload

## Operational Verification

For each server target, log and confirm:

1. Katello version detected.
2. Upload mode chosen.
3. Whether first import succeeded or retry path was used.
4. Exact failure payload when checksum mismatch occurs.

This creates a deterministic decision path for mixed 6.14 and 6.18 fleets.
