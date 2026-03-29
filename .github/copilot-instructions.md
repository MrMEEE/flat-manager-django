# Copilot Instructions for flat-manager-django

## UI / JavaScript

- **Never use `confirm()`, `alert()`, or `prompt()`** — always use a Bootstrap modal instead.
  - For confirmations, use (or replicate) the `showDeleteConfirm(title, body, okLabel, onOk)` pattern found in `templates/flatpak/promotion_list.html`.
  - For error messages, use `showErrorModal(title, message)`.
  - Every template that needs confirmation dialogs must include the corresponding modal HTML (`<div class="modal fade" ...>`) and `showConfirmModal` / `showErrorModal` JS functions.

## Releasing

- **Always use `./release.sh`** to create a release — never do manual `git tag` / `git push` / version edits.
- The script handles everything: version bump in `version.py`, spec `%changelog` entry, commit, tag, and push (which triggers the GitHub Actions RPM build workflow).
  ```
  ./release.sh            # patch bump (default)
  ./release.sh --minor    # minor bump
  ./release.sh --version X.Y.Z  # explicit version
  ./release.sh --dry-run  # preview only
  ```
