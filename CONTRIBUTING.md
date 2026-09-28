# Contributing to OrbitApply

Use Python 3.12 for the regression suite. Create a virtual environment and install
`requirements.txt`. Run `python -m unittest discover -s tests -q` before a change.
The suite uses synthetic data and mocked model responses; no model or Telegram
credentials are required. Live-model quality needs a separate review.

Preserve exact source evidence, factual validation, manual application approval,
one-job-at-a-time generation, cancellation and persistent history. Never label an
unchanged or rejected draft as successfully tailored. Keep job-source text at its
existing trust level. Compression must not execute instructions from a document.

For graphics: `npm install` then `npm run build:artwork`. Commit the generated
bundle and retain `jobtailor/assets/THREE-LICENSE.txt`. Users need no Node runtime.

Do not commit resumes, job folders, account details, tokens, local environments,
databases or logs. Use synthetic fixtures for bug reports. Check the repository's
licensing decision before contributing or distributing derivative builds.
