# Submission checklist

Submission window (from the WeMakeDevs team page, 2026-10-16): opens **October 17, 2026, 1:03 AM IST**,
closes **October 18, 2026, 11:59 PM IST**. The team cannot be changed after submitting.
Never put the team join code in the repository or the video.

## Done in the repository (verified)

- [x] FalkorDB is load-bearing (R2/R3); graph-removed mode and ablations measured
- [x] README with setup, graph data model, Cypher, track, AI disclosure
- [x] `docker compose up -d --build` verified end to end (2026-10-16): pinned FalkorDB 6.0.2, healthcheck, non-root API
- [x] Tests: 138 passing (`pytest tests`) against live FalkorDB
- [x] Benchmarks with raw outputs in `bench/results/` (synthetic, real RCAEval RE1/RE2, scale)
- [x] MCP server verified over stdio (`python -m scripts.mcp_smoke`); local LLM tool selection measured (`bench/eval_llm_agent.py`)
- [x] Screenshots from the running app in `docs/img/`
- [x] Third-party notices; no secrets in the repo (`.env` ignored)

## Only you can do

- [ ] Read `docs/walkthrough.md`; be able to explain the graph model, each Cypher query, the scoring and the max-flow step (Rules 12–13)
- [ ] Decide the legacy files question (old `synapseguard/engine/traversal.py`, `agent.py`, `memory.py`, `api/server.py`, `core/db.py`, `core/schema.py`, `data/seed.py`, `data/scale_seed.py`, `frontend/`, `synapseguard/tests/`): remove them or keep them
- [x] Git author for new commits set to Mohit Shirke (repo-local config). The two earlier commits still show `Independent Developer`; rewriting them is optional
- [x] Team **Night Hunter** registered on WeMakeDevs (solo, leader Mohit Shirke)
- [ ] Create the public GitHub repo and push
- [x] Captioned demo video built from live app screenshots: `submission/synapseguard-demo.mp4` (2:21, not committed)
- [ ] Upload the video (YouTube unlisted or Google Drive), add the link to the submission form and README. Optional: record a narrated version using `docs/demo-script.md`
- [ ] Optional: live deployment (e.g. FalkorDB Cloud + a container host), then add the URL
- [ ] Register on the WeMakeDevs page if not done; submit repo, video, deployment/setup, project info
- [ ] Name the track: **Track 01 only** (see docs/competition.md)
- [ ] Optional side prize: publish `docs/blog-post.md` (edit it into your own voice first)
- [ ] Re-check the rules page for changes before submitting
