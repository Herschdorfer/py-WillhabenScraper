# Repository Instructions

## Writing

- Use plain, natural wording. Avoid semicolons, em dashes, and formulaic AI-sounding prose.
- In prose, put each sentence on one line. Add line breaks only after sentence-ending dots. Keep necessary line breaks in code, tables, headings, and lists.
- Preserve existing user edits. Check the worktree before editing and do not overwrite unrelated changes.

## README

- Describe the current project, usage, configuration, and behavior only.
- Do not include project history, rename narratives, audit histories, PR review order, or completed work checklists.
- Keep the README useful to someone installing, configuring, and running the scraper.
- When asked to review or improve the README, apply these rules directly and preserve the author's current edits.

## Project

- The project is named `web-metrics-scraper` and accepts configurable HTTP(S) sources.
- The Python entry point is `web_metrics_scraper.py`.
- The scraper writes extracted integer metrics to InfluxDB 2.x.
- Keep credentials in a private configuration file mounted read-only into Docker. Never bake credentials into the image or log tokens, URL queries, or credential-bearing URLs.
- Prefer HTTPS. HTTP remains supported for local services.
- The published image is `ghcr.io/herschdorfer/web-metrics-scraper`. Describe only current image and release behavior.
- Follow the existing Python, unittest, Ruff, and pre-commit conventions.

## Git And Pull Requests

- Keep `master` and feature branch history linear. Do not create merge commits. Use squash or rebase merges.
- Do not merge pull requests or change repository protections without explicit user approval.
- Do not force-push or rewrite published branches unless explicitly requested. Preserve published history when updating PRs.
- Keep PRs focused. Validate changes before committing or publishing them.

## Validation

- Run `python -m unittest discover -s tests -v` for behavior changes.
- Run `pre-commit run --all-files` for formatting, lint, security, and workflow checks.
- For dependency changes, run `python -m pip check` and `python -m pip_audit -r requirements.txt`.
- For Docker changes, build the image, smoke-test its `--help` command, and run the offline tests inside the image when practical.
