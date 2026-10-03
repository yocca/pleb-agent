# pleb-agent

Python jobs for Pleb, a happy hour finder. Imports venues from open data, crawls venue websites and processes menu photos, and uses Nebius Token Factory models to extract happy hour schedules into Postgres.

## Local development

Runs from the docker compose setup in `pleb-api`. Secrets (`NEBIUS_API_KEY`) go in a local `.env` that is never committed.

## Specs

This repo uses [OpenSpec](https://github.com/Fission-AI/OpenSpec) for spec-driven changes. Project context and sourcing rules are in `openspec/config.yaml`; current specs are in `openspec/specs/`, proposed changes in `openspec/changes/`. In Claude Code, use `/opsx:propose`, `/opsx:apply` and `/opsx:archive`.
