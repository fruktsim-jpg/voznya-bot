# AGENTS.md

Guidance for AI agents working in this repository.

## Project status

`voznya-bot` is currently a **greenfield stub**: the only tracked file besides this document is `README.md`. There is no application entrypoint, dependency manifest, Docker setup, or test suite yet.

When implementation lands, re-read `README.md`, lockfiles, and any `docker-compose` / `.env.example` files to learn how to install, run, lint, and test.

## Cursor Cloud specific instructions

### Services

| Service | Required | Notes |
|---------|----------|--------|
| *(none)* | — | No dev servers, databases, or containers are defined in the repo yet. |

### Update script (VM startup)

The VM update script is a no-op (`true`) until dependency manifests exist. After adding e.g. `package.json` or `requirements.txt`, change the update script to the appropriate install command (`npm ci`, `pnpm install`, `uv sync`, etc.) via the Cloud Agent environment settings.

### Available VM toolchain (verified 2026-06-04)

Cloud Agent VMs have common runtimes preinstalled for when bot code lands:

| Tool | Version (example VM) |
|------|----------------------|
| Node.js | v22.x (via nvm) |
| npm / pnpm | available on PATH |
| Python | 3.12.x |
| git | 2.43+ |

No project-specific install step runs until a lockfile is committed.

### Lint / test / build / run

Not applicable until source code and tooling are added. Standard commands will appear in `README.md` or package scripts once the bot is implemented.

### Git

- Default branch: `main`
- Remote: `origin` → GitHub `fruktsim-jpg/voznya-bot`

### Gotchas

- Do not assume Python vs Node vs another stack from the repo name alone; check lockfiles and README after the first implementation commit.
- Detached HEAD checkouts should be switched to `main` before branching: `git checkout main`.
