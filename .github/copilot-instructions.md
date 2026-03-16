# Copilot Instructions for flat-manager-django

## UI / JavaScript

- **Never use `confirm()`, `alert()`, or `prompt()`** — always use a Bootstrap modal instead.
  - For confirmations, use (or replicate) the `showDeleteConfirm(title, body, okLabel, onOk)` pattern found in `templates/flatpak/promotion_list.html`.
  - For error messages, use `showErrorModal(title, message)`.
  - Every template that needs confirmation dialogs must include the corresponding modal HTML (`<div class="modal fade" ...>`) and `showConfirmModal` / `showErrorModal` JS functions.
