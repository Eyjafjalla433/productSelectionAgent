# Deployment and GitHub release

## Repository contents

Commit source, tests, public development sessions, evaluator, manifests, and documentation. Do not commit generated environments, results, the 50,000-product catalog, model weights, or embeddings.

The `.gitignore` already excludes those paths.

## Runtime asset

Upload `techjam-runtime-assets-v0.1.0.zip` as a GitHub Release asset named `v0.1.0`. Expected SHA256:

```text
57769c08803b2a68604e12bbc59ec07beea65d831498aa3d361cb0e0a7004f2b
```

The archive installs these roots:

```text
data/catalog.jsonl
resources/bge-small-en-v1.5/
resources/dense_catalog_embeddings/
```

After publishing the Release, set `asset-manifest.json.download_url` to the permanent asset URL and rerun the clean install test.

## Recommended GitHub sequence

```powershell
git init
git add .
git commit -m "feat: publish two-stage TechJam search agent"
gh repo create <owner>/<repo> --private --source . --remote origin --push
gh release create v0.1.0 local-dist/techjam-runtime-assets-v0.1.0.zip `
  --title "v0.1.0 runtime assets" `
  --notes "Verified exact-mode catalog, BGE model, and embeddings."
```

Choose public/private and the repository name before running these external-write commands. If competition rules prohibit public code before judging, keep it private.

## Clean-machine acceptance test

```powershell
py -3.12 -m venv C:\venvs\techjam-agent
C:\venvs\techjam-agent\Scripts\python.exe -m pip install -e ".[exact]"
C:\venvs\techjam-agent\Scripts\python.exe -m scripts.install_assets
C:\venvs\techjam-agent\Scripts\python.exe -m unittest discover -s tests -v
C:\venvs\techjam-agent\Scripts\python.exe -m scripts.validate_pipeline --mode exact
C:\venvs\techjam-agent\Scripts\python.exe -m scripts.evaluate_full_requirements
```

Expected final checks:

- 6 unit tests pass.
- Top50 coverage is 199/200.
- Top10 hit count is 198/200.
- Counterfactual three-turn HitRate@10 is 0.99.
- No network access is required after asset installation.
