# Releasing isolate-elf

## One-time PyPI setup

### 1. Create the PyPI project

Go to https://pypi.org and create a new project named `isolate-elf` (or
whatever the package name will be). You can do this by uploading a first
release manually, or the trusted publisher can create it on first push.

### 2. Configure trusted publishing

On PyPI, go to your project > Manage > Publishing and add a new trusted
publisher:

| Field | Value |
|-------|-------|
| Owner | `stellaraccident` |
| Repository | `isolate-elf` |
| Workflow name | `release.yml` |
| Environment name | `pypi` |

This tells PyPI to accept OIDC tokens from this specific GitHub Actions
workflow. No API tokens needed.

### 3. Create GitHub environment

In the GitHub repo: Settings > Environments > New environment > name it
`pypi`. No additional protection rules needed (the tag-push trigger is
the gate), but you can optionally add required reviewers if you want a
manual approval step before publishing.

## Making a release

```bash
# 1. Make sure you're on main with a clean tree
git checkout main
git pull
git status  # must be clean

# 2. Run the release script (dry-run first)
python build_tools/make_release.py --version 0.1.0 --bump-dev --dry-run

# 3. Do it for real
python build_tools/make_release.py --version 0.1.0 --bump-dev

# 4. Push
git push origin main --tags
```

The `make_release.py` script:
1. Sets `version.json` and `src/isolate_elf/__init__.py` to `0.1.0`
2. Commits: `Release v0.1.0`
3. Tags: `v0.1.0`
4. Sets version to `0.2.0.dev0` (with `--bump-dev`)
5. Commits: `Bump to 0.2.0.dev0`

Pushing the tag triggers `.github/workflows/release.yml` which:
1. Runs the full test suite (unit + integration)
2. Builds sdist + wheel
3. Publishes to PyPI via OIDC trusted publishing

## Version scheme

- `X.Y.Z` — release versions (on PyPI)
- `X.Y.Z.dev0` — development versions (in main between releases)

## Testing on TestPyPI first

To test the release pipeline without publishing to real PyPI, you can
temporarily modify `release.yml` to use TestPyPI:

```yaml
- name: Publish to TestPyPI
  uses: pypa/gh-action-pypi-publish@release/v1
  with:
    packages-dir: dist/
    repository-url: https://test.pypi.org/legacy/
```

You'll need a separate trusted publisher configured on test.pypi.org.
