# Contributing

This repository is split into modules so teammates can work independently
without changing another module's behavior accidentally.

## Workflow

1. Update local `main` before starting.
2. Create a focused branch such as `feature/state-memory` or
   `fix/retrieval-cap`.
3. Change only the relevant module and keep its public interface documented.
4. Run that module's tests from its own directory.
5. Commit with a descriptive message and open a pull request to `main`.
6. In the PR, state the assumption, commands run, exact numerator/denominator,
   limitations, and any interface change another teammate must consume.

Do not push feature work directly to `main`, even though GitHub currently
allows it. The repository owner should review and merge pull requests.

## Module boundaries

- `intent-recognition/` understands the current user message; it does not own
  session state.
- `conversation-state-memory/` owns multi-turn accumulation, overrides,
  exclusions, and the context supplied to downstream modules.
- `retrieval-and-reranking/` defines the candidate-retrieval contracts and
  routes. The submitted integrated profile supplies a recall-compatible Top-50
  pool before CPU ranking returns up to Top-10.
- Offline evaluators may join labels after inference. Runtime modules must not
  read targets, hidden intent cards, private holdout data, or future messages.

## Tests

Run tests from the module you changed. For retrieval and reranking:

```bash
cd retrieval-and-reranking
python -m unittest discover -s tests -v
python -m scripts.validate_pipeline --mode exact
```

Always distinguish full-card offline diagnostics from official interactive
evaluation. Report counts such as `198/200`, not only rounded percentages.

## Data, models, and secrets

Do not commit:

- the 50,000-product catalog;
- model weights or embedding arrays;
- virtual environments, caches, or generated result folders;
- API keys, access tokens, credentials, or private organizer sessions.

Use a reviewed Release asset or another team-approved store for large runtime
artifacts. Synthetic data must include its lineage and must not be described as
official data or as reconstructed private holdout sessions.
