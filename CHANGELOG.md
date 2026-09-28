# Changelog

## 0.17.0: OrbitApply

- Rebranded app title, navigation, landing footer, launcher messages and setup docs.
- Preserved the sci-fi skin, Mac/Windows launch behavior, serial queue and history.
- Added conservative schema compression and compact structured prompt JSON.
- Added actual Ollama usage reporting with explicit coverage and estimated payload
  reduction; retained unknown speedup/savings as unknown.
- Corrected cache estimates that previously included reserved output capacity.
- Added compression regression tests, offline measurement script, GitHub CI,
  privacy/contribution docs, third-party inventory and publishing instructions.
- Did not incorporate an external compressor package, change audit thresholds,
  migrate application history, publish a repository or add a runtime dependency.

Earlier implementation history is retained in `RELEASE_NOTES.md`.
