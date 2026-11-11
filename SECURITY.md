# Security policy

## Reporting a vulnerability

Please don't open a public issue for a security problem. Report it privately through GitHub: open the repository's **Security** tab and choose **Report a vulnerability**.

Include:
- what is affected (a file, route or tool) and the version or commit;
- the steps to reproduce it;
- the impact you expect.

We'll acknowledge your report, keep you informed while we work on a fix, and credit you when it's released, unless you'd rather stay anonymous.

## Supported versions

Fixes go into the latest commit on `main`. There are no maintained release branches yet.

## Scope

JonaAI runs AI agents that call tools, build OpenEO process graphs and read and write files. Reports we especially want:
- a way for one user to read or change another user's data;
- a process graph or tool argument that escapes the gates: fetching a URL, running code, reaching outside the result folders, or getting past the area and size limits;
- authentication or API-key bypasses;
- server-side request forgery through delivery targets.

Running your own deployment securely is described in [`docs/deployment.md`](docs/deployment.md): bind every listener to loopback, put the app behind an identity-aware proxy, and keep secrets out of the repository.
