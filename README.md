# AgriCredit Agent

**Evidence-first agricultural input and credit risk advisory for local extension offices and cooperatives.**

An evidence-first agricultural underwriting agent that helps extension officers match farming households to input packages and credit offers they can realistically service. It is not an autonomous lender, a replacement for the officer, or a farmer-facing chatbot. It drafts; a named officer decides.

Built new for the Qhala Trust African Agentic AI Design Challenge (Agriculture & Food Security track). Benin-focused, amounts in FCFA. **All household, farm and repayment data is synthetic.**

## What it does

For a credit case (a household, an input package, loan terms), the agent:

1. reads the decision policy through the **official MCP filesystem server**;
2. plans the investigation with an **open-weight Qwen model**;
3. gathers household, yield, repayment, rainfall and market evidence through **our own MCP server**, retrying and recovering from failures within fixed bounds;
4. computes serviceability with **deterministic finance, four stress scenarios and a calibrated risk model** (no LLM arithmetic);
5. checks for contradictions and missing or stale evidence, and **refuses to recommend** when evidence is insufficient;
6. drafts an advisory in which every figure cites a record ID, then **stops** at a human-approval interrupt;
7. releases (to a sandbox outbox; nothing is ever sent) **only** if a registered officer approved that exact draft and nothing has changed since.

Every MCP and LLM call is logged with inputs, outputs, latency and timestamps.

## Run it (one command)

Requirements: Python ≥ 3.11, Node.js ≥ 20, and git. Linux or macOS. On Windows, use WSL (see below).

```bash
git clone <this repo> agricredit-agent && cd agricredit-agent
./run_demo.sh            # sets up, runs the tests, opens the officer dashboard at http://localhost:8501
./run_demo.sh --cli      # same setup, then the scripted terminal demo
```

The first run creates `.venv`, installs the pinned `requirements.lock` and `package-lock.json`, generates the synthetic dataset and trains the risk model (all offline, seeded, about a minute). It then runs the test suite.

**Tested:** a fresh clone with only Python 3.13 and Node 22 present ran `./run_demo.sh --cli` successfully in the build environment. See "Verification status" below for what that environment could not test.

### With the open-weight model (Qwen)

```bash
# 1. install Ollama (https://ollama.com/download), then:
ollama pull qwen2.5:7b-instruct
# 2. .env already defaults to AGRICREDIT_LLM_BACKEND=ollama
./run_demo.sh
# 3. capture evidence of one complete task run by the open-weight model:
./scripts/run_open_weight_task.sh            # writes open_weight_runs/<timestamp>_BEN-0421/
make evals                                   # re-runs all evaluation tasks with Qwen and regenerates EVALS.md
```

If no Ollama server answers, `run_demo.sh` falls back to `stub`: a deterministic stand-in that is **not a language model**, labelled as such in the dashboard, logs and advisories. It exists so the graph, MCP servers, risk model, gate and audit trail can be inspected and tested without a model runtime. vLLM or llama.cpp serving Qwen also work through `AGRICREDIT_LLM_BACKEND=openai_compatible`.

**No GPU?** `qwen2.5:7b-instruct` runs on CPU with about 8 GB of free RAM, but each of the two LLM calls can take a few minutes. Raise `AGRICREDIT_LLM_TIMEOUT_S` in `.env` (for example to 600) or use `OLLAMA_MODEL=qwen2.5:3b-instruct`. The model identifier and digest are recorded in every run either way.

### On Windows

Native Windows has not been tested. Use WSL 2, where the tested Linux path applies unchanged, and run Ollama inside WSL so `localhost:11434` resolves:

```bash
# in PowerShell (admin), once:  wsl --install -d Ubuntu      then open the Ubuntu terminal
sudo apt update && sudo apt install -y python3-venv git curl
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt install -y nodejs
curl -fsSL https://ollama.com/install.sh | sh          # Ollama inside WSL
git clone <this repo> agricredit-agent && cd agricredit-agent
./run_demo.sh --cli                                    # first run: setup + tests + scripted demo
./scripts/run_open_weight_task.sh                      # one full task with Qwen, evidence saved to open_weight_runs/
./run_demo.sh                                          # dashboard: open http://localhost:8501 in your Windows browser
```

Clone into the WSL home directory (`~`), not `/mnt/c/...`: the Windows drive is much slower from WSL.

## Verification status

What was and was not verified in the build environment (a cloud sandbox without GPU, Docker Hub or model-host access):

| Requirement | Status | Evidence |
|---|---|---|
| Own MCP server, 7 tools (5 read, 2 action) | Verified | `tests/test_mcp_server.py` (over stdio and in-process) |
| Borrowed MCP server (official filesystem server) | Verified | policy read + audit export on every run; E17 path-traversal denial |
| LangGraph orchestration with human-approval interrupt | Verified | `tests/test_agent_graph.py`, EVALS |
| Risk model, stress tests, evidence citations | Verified | `tests/test_financial.py`, `data/model_card.json`, EVALS metrics |
| Tool-call and audit logging | Verified | `logs/*.jsonl` from a real demo run; EVALS log-completeness metric |
| Approval gate: unauthorized releases blocked, change voids approval | Verified | E11, E12, `tests/test_mcp_server.py` |
| **Open-weight model completes a full task** | **Not verified here** | Hugging Face, Ollama and ModelScope were unreachable, so Qwen could not be downloaded. Backend code is protocol-tested against a fake Ollama endpoint (`tests/test_llm_backend.py`). Run `scripts/run_open_weight_task.sh` to produce this evidence. |
| One-command run from a clean clone | Verified with `--cli` | Fresh clone, Python 3.13, Node 22 |
| Docker | **Not verified** | Docker Hub was unreachable, so the image could not be built. `Dockerfile` and `docker-compose.yml` are provided but untested. |

The committed `logs/` and `EVALS.md` come from runs with the **offline stub**. They say so in every record.

## Dashboard

Eight panels, in reading order: case selection (sidebar, including a toggle that simulates a rainfall-service outage); **what the agent did** (each graph step and the MCP tool it called); **evidence retrieved** (kind, record ID, observation date, staleness); **risk assessment** (score, PD, capacity, and the features driving the risk); **stress test**; **draft advisory** (recommendation, rationale, warnings, missing evidence, officer action, LLM explanation, assumptions, hashes); **officer decision**; **audit trail**. The three-minute walkthrough is in [`docs/DEMO.md`](docs/DEMO.md).

## MCP servers

| Server | Who maintains it | Tools used |
|---|---|---|
| `agricredit-mcp` (`python -m agricredit.mcp_server`) | this project | `get_credit_case`, `get_household_case`, `get_weather_signal`, `get_market_signal`, `assess_serviceability`, `draft_credit_advisory` (action), `release_advisory_after_approval` (action) |
| `@modelcontextprotocol/server-filesystem` 2026.8.31 | Model Context Protocol project | `read_text_file` (policy, eval fixtures), `create_directory`, `write_file` (audit exports), restricted to `fs_mcp_root/` |

Built on MCP Python SDK 2.3.0 (`MCPServer`, `Client`); the deprecated v1 `FastMCP` API is not used.

## Recommendations the agent can draft

`RECOMMEND`, `RECOMMEND_WITH_CONDITIONS`, `REDUCE_CREDIT_AMOUNT`, `REQUEST_MORE_EVIDENCE`, `NO_RECOMMENDATION`. The type is chosen by deterministic policy (`src/agricredit/policy.py`, thresholds in `fs_mcp_root/policy/agent_policy.json`); the LLM explains it. The server rejects any draft more permissive than the evidence supports, and strips credit-decision language ("APPROVED CREDIT", "DISBURSE") from model text.

## Evaluation

[`EVALS.md`](EVALS.md) is generated by `evals/run_evals.py` from an actual run: 18 tasks, covering the 12 required scenarios plus stale market data, missing repayment history, the server-side guard, tampered evidence, path traversal and run-to-run variation. With the offline stub, 17 of 18 pass. The failure (E04: reduced offers are fractions of a package rather than real catalogue packages) is a genuine, documented, unresolved limitation, with the next fix described.

## Repository layout

```
src/agricredit/
  agent/        graph.py (LangGraph), runner.py, toolbox.py (MCP clients + logging), llm.py (Qwen backends)
  mcp_server/   server.py (agricredit-mcp)
  risk/         financial.py, assess.py, model.py, train.py, assumptions.py
  approval/     gate.py (approval recording, verification, sandbox release)
  audit/        logger.py (JSONL tool-call and audit logs)
  ui/app.py     Streamlit dashboard
  cli/          python -m agricredit.cli {run,demo,cases}
  policy.py, services.py, integrity.py, models/schemas.py, config.py, db.py
data/           schema, synthetic generator, model card, data README
fs_mcp_root/    the only directory the filesystem MCP server may touch: policy/, evals/, audit_exports/
evals/          run_evals.py, template, results.json
tests/          44 tests: finance, MCP server, gate, agent graph, LLM backends, dashboard
logs/           tool_calls.jsonl, audit_events.jsonl from the committed demo run
scripts/        run_open_weight_task.sh
```

## Security notes

Household notes, market records and retrieved files are treated as untrusted data. Defences, with the eval that tests each: strict Pydantic validation with ID regexes (E17); injection screening and withholding notes from the model (E10); HMAC-signed evidence (E16); server-side policy re-check (E15); filesystem access only via the sandboxed official server (E17); no MCP tool can create an approval; approvals are single-use and bound to hash, evidence and model version (E11, E12).

## License

Apache-2.0 (see `LICENSE`). Dependencies keep their own licences.
