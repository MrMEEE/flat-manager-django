# Ansible

This directory wires up the **[mrmeee.flatman](https://github.com/MrMEEE/ansible_collection.flatman)**
collection, which wraps the complete flat-manager-django REST API.

The collection itself lives in its own repository and is vendored here as a git
submodule, so it can be released to Galaxy independently of the server.

```
ansible/
├── ansible.cfg                                  # points collections_path here
├── requirements.yml
├── inventory/localhost.yml                      # the modules run on the controller
└── collections/ansible_collections/mrmeee/flatman/   <-- submodule
```

## First checkout

```bash
git submodule update --init
```

Existing clones that predate the submodule need the same command once.

## Running an example

```bash
cd ansible
export FLATMAN_URL=https://flatman.example.com
export FLATMAN_API_TOKEN=...        # create one under Profile in the web UI
ansible-playbook collections/ansible_collections/mrmeee/flatman/playbooks/smoke.yml
```

`ansible.cfg` sets `collections_path = ./collections`, so the collection
resolves without `ansible-galaxy install`.

## Documentation

Everything lives in the submodule:

- [Collection README](collections/ansible_collections/mrmeee/flatman/README.md) — module index
- [Getting started](collections/ansible_collections/mrmeee/flatman/docs/getting-started.md)
- [Authentication](collections/ansible_collections/mrmeee/flatman/docs/authentication.md)
- [Endpoint to module map](collections/ansible_collections/mrmeee/flatman/docs/endpoint-map.md)
- [Idempotency and check mode](collections/ansible_collections/mrmeee/flatman/docs/idempotency.md)

## Tests

```bash
cd ansible/collections/ansible_collections/mrmeee/flatman
ansible-test sanity --python 3.12 --local
```

## Updating the collection

Changes to the modules are committed in the collection repository, then the
pointer here is bumped:

```bash
cd ansible/collections/ansible_collections/mrmeee/flatman
git checkout main && git pull
cd -
git add ansible/collections/ansible_collections/mrmeee/flatman
git commit -m "Bump mrmeee.flatman collection"
```
