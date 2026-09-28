# Security and privacy

OrbitApply is a local, single-user application. Keep the server bound to
127.0.0.1. It has no multi-user authentication or hosted service security layer.

Never post resumes, application history, Telegram tokens or full runtime logs in
a public issue. Send a minimal synthetic reproduction through the repository
owner's private reporting channel once configured. No contact address is
preconfigured in this source package.

The default Ollama endpoint is local. Job searches send role titles, keywords,
locations and page requests to external websites. Telegram notifications are
optional and send the fields described in the Privacy page. Output folders and
the application-history database are local files, not encrypted by this app.

Content compression adds no network dependency and stores numeric diagnostics
only. It never shortens raw resume/JD text or bypasses factual validation. Model
checks can still miss errors; review drafts before applying.
