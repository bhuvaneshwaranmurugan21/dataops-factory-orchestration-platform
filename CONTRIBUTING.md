# Contributing

Changes must preserve the evidence boundary and deterministic plan. Run:

```bash
make evidence
make verify
make package
```

If the registry changes, regenerate `orchestration/generated/compiled-plan.json` with the intended
registry commit token and refresh local evidence. New retry, lease, callback or publication behavior
requires an adversarial test. Never commit credentials, generated datasets, SQLite databases,
Terraform state or cloud output presented as verified evidence without its provenance.
