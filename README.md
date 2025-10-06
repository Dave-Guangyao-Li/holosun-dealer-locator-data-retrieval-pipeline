# Holosun Dealer Locator Data Pipeline

Backend-focused project that automates retrieval of Holosun dealer listings for all California ZIP codes, normalizes the results, and exports an auditable CSV dataset. Documentation lives in `docs/`.

## Repository Layout
- `docs/`: planning notes, design decisions, and change log.
- `src/holosun_locator/`: Python package scaffold for future automation code.
- `scripts/`: command-line entry points and utilities (to be implemented).
- `config/`: environment templates and runtime configuration.
- `data/`: stores raw and processed datasets (ignored by git except for placeholders).
- `logs/`: runtime logs, including manual-intervention prompts.
- `tests/`: automated test suite scaffold.

## Getting Started
1. Create and activate a Python 3.11+ virtual environment.
2. Install dependencies (to be defined in `pyproject.toml`).
3. Populate `config/settings.env` based on `config/settings.example.env` once available.
4. Follow the workflow described in `docs/project-notes.md` before running any scraping tasks.

Refer to `docs/project-notes.md` for detailed requirements, architecture, and TODO backlog.
