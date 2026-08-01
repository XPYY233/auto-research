# Desktop product and portable evidence-package contract

## Product direction

- Current development preview: macOS, validated on the developer's local Mac.
- Intended end-user platform: Windows desktop, with macOS kept as a supported development/test target where practical.
- Product interface: one personal research workbench desktop App. Browser workbench, mentor read-only page, localhost ports and ngrok are not user products.
- Internal implementation may use a loopback service and embedded web UI, but users must not need to start a terminal, know a port or enter an Auto Research edit password.

The shared core, evidence-package format and user data model must stay platform-neutral. Do not put Apple-only paths, WKWebView assumptions, Zotero storage keys or Keychain identifiers into scientific data or package manifests.

## Intended non-technical user flow

1. Install the desktop App for the current platform.
2. Receive an evidence package supplied by the project team.
3. Choose **Import evidence package** in the App.
4. The App checks package format version, manifest, cryptographic hash and signature before changing local data.
5. After a successful import, search, evidence browsing and supported exports work offline.
6. The user may also upload their own lawful PDF and run extraction into a separate private library.
7. Only when an AI feature is first used does the App guide the user through API-key configuration.

Import failure must leave the previous library usable. A staged import is validated before activation, and the previous package version remains available for rollback.

## Package boundary

The planned portable extension is provisionally named `.aresearch`. Its implementation is the next product phase after the stable desktop preview; this document fixes the contract only.

An official package may contain:

- a versioned, sanitized evidence database;
- a manifest with package ID, schema version, compatible App versions and content hashes;
- provenance records using DOI, normalized title, stable evidence IDs and package-relative asset IDs;
- assets that have passed redistribution and privacy review;
- package license and limitation metadata.

By default it must not contain:

- restricted or privately obtained PDF files;
- developer-machine absolute paths;
- Zotero or device-local keys;
- private conversations, review identities or internal notes;
- developer DeepSeek credentials or any other secret;
- files whose redistribution scope has not been reviewed.

The official package, user's private library and App installation are separate update domains. Replacing or upgrading an official package must not overwrite user-uploaded PDFs, private extraction results, settings or conversations.

## AI key and data-use onboarding

Both AI capabilities use BYOK:

- extracting a newly uploaded paper;
- asking the Librarian Agent.

The App must never ship or silently reuse the developer's API key. A user may paste a key supplied specifically to them or obtain their own key from the configured provider. Before the first AI request, show a short step-by-step guide that explains:

1. **What the key is:** a credential authorizing requests to the configured DeepSeek account.
2. **Who receives the request:** DeepSeek's API service, not the evidence package author.
3. **Possible cost:** usage may consume paid tokens under the account associated with that key; the App should show the selected model and provide a cost/usage link where available.
4. **What leaves the computer:** for extraction, bounded relevant text pages and structured instructions; for Librarian, the user's question and bounded structured evidence. The whole library, unrestricted PDFs and unrelated private files are not sent by default.
5. **Where the key is stored:** the operating system secure credential store—macOS Keychain in the current preview and Windows Credential Manager in the intended Windows build.

The key must not be written to SQLite, `.aresearch` packages, logs, crash reports, exported briefs, browser storage or Git. Removing or replacing a key must not modify scientific evidence.

## Cross-platform engineering constraints

- Use platform-neutral relative resource IDs and portable manifests.
- Keep application binaries, official packages and user-private data in separate directories.
- Resolve user data and cache locations through a platform abstraction, not hard-coded macOS paths.
- Abstract secure credential storage behind one interface; use Keychain on macOS and Credential Manager on Windows.
- Keep the core Python/SQLite/Search V2/evidence contracts independent from the desktop shell.
- Treat the current macOS App as a development preview, not proof that Windows packaging, updates and clean-machine installation are complete.

## Release sequence

1. Freeze the shared core and deliver one stable macOS development preview.
2. Define and test sanitized `.aresearch` export/import with rollback.
3. Add the non-technical AI-key onboarding and platform credential abstraction.
4. Build the Windows desktop shell without changing scientific-data semantics.
5. Validate on a clean Windows machine: install, package import, offline search, private PDF upload, BYOK extraction/Librarian, update and rollback.
6. Only then describe the product as ready for external Windows users.
