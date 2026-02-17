# Repository Subset Management

## Overview
Repository subsets are now fully implemented with CRUD (Create, Read, Update, Delete) functionality. Subsets allow creating partial repository mirrors with different collection IDs, matching the flat-manager architecture.

## Features Implemented

### 1. Repository Creation with Subsets
- Create new repositories with collection_id (replaced default_branch)
- Add multiple subsets during repository creation
- Dynamic form for adding subsets with JavaScript

### 2. Repository Editing
- Edit existing repositories at `/repos/<id>/edit/`
- Update collection_id, description, and GPG key
- View existing subsets in edit form
- Add new subsets while editing

### 3. Subset Management from Repository Detail Page
- View all subsets for a repository
- "Add Subset" button to create new subsets
- Edit and Delete buttons for each subset
- Shows collection_id and base_url for each subset

### 4. Individual Subset CRUD Operations

#### Create Subset
- URL: `/repos/<repo_id>/subsets/create/`
- Form fields:
  - Name (required)
  - Collection ID (required) - reverse DNS format
  - Base URL (optional) - for subset mirror location

#### Edit Subset
- URL: `/subsets/<id>/edit/`
- Update subset name, collection_id, and base_url
- Returns to repository detail page after save

#### Delete Subset
- URL: `/subsets/<id>/delete/`
- Confirmation page showing subset details
- Permanent deletion with warning message

### 5. API Endpoints
- `GET /api/repository-subsets/` - List all subsets
- `GET /api/repository-subsets/<id>/` - Get subset details
- `POST /api/repository-subsets/` - Create new subset
- `PUT /api/repository-subsets/<id>/` - Update subset
- `DELETE /api/repository-subsets/<id>/` - Delete subset
- `GET /api/repositories/<id>/subsets/` - Get all subsets for a repository

### 6. Database Model
```python
class RepositorySubset(models.Model):
    repository = models.ForeignKey(Repository, related_name='subsets')
    name = models.CharField(max_length=255)
    collection_id = models.CharField(max_length=255)
    base_url = models.URLField(blank=True, null=True)
    
    class Meta:
        unique_together = [['repository', 'name']]
```

## Usage Examples

### Creating a Repository with Subsets (Web UI)
1. Navigate to Repositories → New Repository
2. Fill in repository name and collection_id
3. Click "Add Subset" button
4. Fill in subset details (name, collection_id, base_url)
5. Add more subsets as needed
6. Click "Create Repository"

### Adding Subsets to Existing Repository
1. Navigate to repository detail page
2. Click "Add Subset" button in the Subsets section
3. Fill in subset form
4. Click "Create Subset"

### Editing a Subset
1. Navigate to repository detail page
2. Click the edit (pencil) icon next to a subset
3. Update subset fields
4. Click "Update Subset"

### Via API
```bash
# Create a subset
curl -X POST http://localhost:8000/api/repository-subsets/ \
  -H "Content-Type: application/json" \
  -d '{
    "repository": 1,
    "name": "stable",
    "collection_id": "org.example.MyApp.Stable",
    "base_url": "https://repo.example.com/stable"
  }'

# List subsets for a repository
curl http://localhost:8000/api/repositories/1/subsets/

# Update a subset
curl -X PUT http://localhost:8000/api/repository-subsets/1/ \
  -H "Content-Type: application/json" \
  -d '{
    "name": "stable",
    "collection_id": "org.example.MyApp.Stable.Updated",
    "base_url": "https://repo.example.com/stable-new"
  }'

# Delete a subset
curl -X DELETE http://localhost:8000/api/repository-subsets/1/
```

## Templates Created
- `templates/flatpak/subset_form.html` - Create/edit subset form
- `templates/flatpak/subset_confirm_delete.html` - Delete confirmation

## Views Added
- `RepositoryUpdateView` - Edit existing repository
- `RepositorySubsetCreateView` - Create new subset
- `RepositorySubsetUpdateView` - Edit existing subset
- `RepositorySubsetDeleteView` - Delete subset

## URL Patterns Added
```python
path('repos/<int:pk>/edit/', RepositoryUpdateView, name='repo_edit')
path('repos/<int:repo_pk>/subsets/create/', RepositorySubsetCreateView, name='subset_create')
path('subsets/<int:pk>/edit/', RepositorySubsetUpdateView, name='subset_edit')
path('subsets/<int:pk>/delete/', RepositorySubsetDeleteView, name='subset_delete')
```

## Flat-Manager Compatibility
The subset implementation matches flat-manager's design:
- Repository has collection_id (not default_branch)
- Subsets have their own collection_id and base_url
- Unique constraint on (repository, name)
- Subsets can be used for partial repo mirrors

## Next Steps
- Implement OSTree repository initialization with collection-id support
- Add subset filtering to build publishing
- Implement subset-specific flatpakref generation
- Add subset to build artifact routing
