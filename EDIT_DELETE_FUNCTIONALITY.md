# Build Edit and Delete Functionality

## Overview
Added comprehensive edit and delete functionality for builds with state-based restrictions and proper UI integration.

## Features Implemented

### 1. Build Editing (BuildUpdateView)
- **Location**: `apps/flatpak/views.py` (lines ~430-472)
- **URL**: `/builds/<id>/edit/`
- **Fields**: `app_id`, `branch`, `arch`

#### Restrictions:
- Only builds in `pending`, `failed`, or `cancelled` status can be edited
- Form fields are disabled for builds in other states
- Shows warning message if build cannot be edited

#### Behavior:
- Validates status before allowing updates
- Shows success message after edit
- Redirects to build detail page after save

### 2. Enhanced Build Deletion (BuildDeleteView)
- **Location**: `apps/flatpak/views.py` (lines ~476-504)
- **URL**: `/builds/<id>/delete/`

#### Smart Delete Logic:
1. **Cancel** (for active builds):
   - Applies to: `pending`, `building`, `committing`, `publishing`
   - Sets status to `cancelled`
   - Keeps build record in database
   - Shows as "Cancel" in UI

2. **Delete** (for completed builds):
   - Applies to: `failed`, `cancelled`, `published`
   - Actually deletes the build from database
   - TODO: Add cleanup for build artifacts
   - Shows as "Delete" in UI

### 3. UI Integration

#### Build List Page (`templates/flatpak/build_list.html`)
Added action buttons in the Actions column:
- **View** (eye icon) - Always visible
- **Edit** (pencil icon) - Only for pending/failed/cancelled builds
- **Cancel** (x-circle icon) - Only for pending/building/committing/publishing builds
- **Delete** (trash icon) - Only for failed/cancelled/published builds

#### Build Detail Page (`templates/flatpak/build_detail.html`)
Added buttons in the header next to status badge:
- **Edit** button - Only for pending/failed/cancelled builds
- **Cancel** button - Only for active builds (with confirmation)
- **Delete** button - Only for completed builds

#### Build Form (`templates/flatpak/build_form.html`)
Enhanced to handle both create and edit modes:
- Dynamic title: "Create New Build" or "Edit Build"
- Dynamic breadcrumbs showing edit path
- Warning message if build is not editable
- Context variable: `edit_mode`

#### Confirmation Page (`templates/flatpak/build_confirm_delete.html`)
Updated to distinguish between cancel and delete:
- Dynamic title and messages
- Different warning text for cancel vs delete
- Context variables: `can_cancel`, `can_delete`

## Usage Examples

### Editing a Build
```python
# Only works for pending, failed, or cancelled builds
# Navigate to: /builds/123/edit/
# Change app_id, branch, or arch
# Submit form
```

### Canceling an Active Build
```python
# For pending, building, committing, or publishing builds
# Navigate to: /builds/123/delete/
# Confirm cancellation
# Build status changed to 'cancelled'
```

### Deleting a Completed Build
```python
# For failed, cancelled, or published builds
# Navigate to: /builds/123/delete/
# Confirm deletion
# Build removed from database
```

## Status Matrix

| Status      | Can Edit | Can Cancel | Can Delete |
|-------------|----------|------------|------------|
| pending     | ✅       | ✅         | ❌         |
| building    | ❌       | ✅         | ❌         |
| built       | ❌       | ❌         | ❌         |
| committing  | ❌       | ✅         | ❌         |
| committed   | ❌       | ❌         | ❌         |
| publishing  | ❌       | ✅         | ❌         |
| published   | ❌       | ❌         | ✅         |
| failed      | ✅       | ❌         | ✅         |
| cancelled   | ✅       | ❌         | ✅         |

## Testing Checklist

- [ ] Edit a pending build - should work
- [ ] Try to edit a building build - should show warning
- [ ] Cancel a building build - should set status to cancelled
- [ ] Delete a failed build - should remove from database
- [ ] Verify buttons show/hide based on status
- [ ] Check WebSocket updates after edit/cancel/delete
- [ ] Verify periodic task still works after edits

## Future Enhancements

1. **Build Artifact Cleanup**
   - Add logic to delete build files/logs when deleting builds
   - Implement in `BuildDeleteView.delete()` method
   - TODO comment already in code

2. **Bulk Operations**
   - Add ability to cancel/delete multiple builds at once
   - Checkbox selection in build list

3. **Status Transitions**
   - Add validation for status transitions
   - Prevent invalid status changes

4. **Audit Trail**
   - Log who edited/cancelled/deleted builds
   - Show edit history

## Code Files Modified

1. `apps/flatpak/views.py` - Added BuildUpdateView, enhanced BuildDeleteView, added HttpResponseRedirect import
2. `apps/flatpak/urls.py` - Added build_edit URL pattern
3. `templates/flatpak/build_list.html` - Added edit/delete buttons
4. `templates/flatpak/build_detail.html` - Added edit/delete buttons in header
5. `templates/flatpak/build_form.html` - Added edit mode handling
6. `templates/flatpak/build_confirm_delete.html` - Enhanced for cancel vs delete

## Services Restarted
All services restarted successfully:
- Django: 357742
- Celery Worker: 357743
- Celery Beat: 357744
- Daphne: 357745
