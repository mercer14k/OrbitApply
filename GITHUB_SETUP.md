# Put OrbitApply on GitHub

1. Extract this archive. Use its `OrbitApply` folder as the repository root.
2. Review `THIRD_PARTY_NOTICES.md` and select a license for your original code.
3. Create an empty GitHub repository named `orbitapply`. Start private while
   reviewing the release. Do not initialize it with another README.
4. In Terminal or PowerShell, change into the extracted folder and run:

```sh
git init
git add .
git status --short
```

5. Check that only source, tests, docs and bundled graphics are staged. No resume,
   outputs, credentials, local environment or database should appear. Then run:

```sh
git commit -m "Initial OrbitApply release"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/orbitapply.git
git push -u origin main
```

Replace YOUR_USERNAME with your actual account. GitHub may ask you to authenticate.
Nothing in this download publishes a repository automatically. The CI workflow
runs regression tests after you push; those remote jobs have not run yet.

Suggested description: Local AI job discovery and evidence-based resume
preparation for macOS and Windows, with serial tailoring and transparent usage.
