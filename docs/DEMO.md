# Three-minute demo script

The demo video is **one continuous, unedited recording** of the real system: no cuts, no speed-up, no slides. It uses one case, BEN-0421, with the rainfall-service outage switched on, so a single run shows planning, MCP tool calls, a tool failure and recovery, the risk model and stress tests, a refusal to recommend, officer approval, the gated release and the audit trail.

## Setup (not recorded)

1. Window A: `ollama serve` (optionally `OLLAMA_KEEP_ALIVE=-1 ollama serve` so the model stays loaded).
2. Window B: `cd ~/agricredit-agent && ./run_demo.sh`, then open `http://localhost:8501`.
3. Set `OLLAMA_MODEL=qwen2.5:1.5b-instruct` in `.env`. On a CPU-only laptop a full run takes about 1 min 40 s with this model, which fits in three minutes. With `qwen2.5:7b-instruct` a run takes about 8 minutes, so it cannot be shown unedited in three minutes; the 7B run is recorded separately in `open_weight_runs/`.
4. Rehearse once (this also loads the model into memory), press F5 to reset the page, then record within five minutes.

## Script

| Time (approx.) | Do | Say |
|---|---|---|
| 0:00–0:20 | Point at the model badge (green, `ollama:qwen2.5:1.5b-instruct`). Check **BEN-0421** is selected. Tick **Simulate rainfall-service outage**. Press **Run agent**. | "This is AgriCredit Agent, an evidence-first credit advisory agent for agricultural extension officers in Benin. All data is synthetic. It runs locally on this laptop's processor with the open-weight model Qwen 2.5. A household is asking for Maize Input Package B, 185,000 FCFA over six months. I'm switching on a deliberate data problem: the rainfall service for this household's zone is down." |
| 0:20–2:00 | Wait while the agent runs. The screen barely changes. | "The agent reads the lending policy through the official MCP filesystem server, then Qwen plans the investigation. It calls our own MCP server for the household's plots, harvests and loan history, then the rainfall signal. That call fails, so it retries once; when it fails again, policy allows a cached reading, which it marks stale. Then it fetches maize prices. A calibrated statistical model, not the language model, calculates the probability of default, and four stress tests run. The agent checks whether the evidence is good enough; it will not invent a rainfall figure. Qwen writes the explanation and the agent stops. Only a named officer can release anything." |
| ~2:00 | Verdict line under the status bar; panel 2 weather lines (two failed attempts, cached reading marked stale); panel 3 record IDs. | "The agent refuses to recommend credit and asks for more evidence. The rainfall tool failed twice, so it fell back to a cached reading, 25 days old, marked stale. Every piece of evidence has its record ID." |
| ~2:15 | Panel 4 risk score; panel 5 red bar below the dashed 1.0 line. | "The risk score comes from a statistical model, not from the AI. Under the combined shock, repayment capacity drops below 1.0." |
| ~2:25 | Panel 6: REQUEST MORE EVIDENCE, the red "Missing evidence" box, the yellow 47-day warning, officer action. | "Because the cached rainfall is older than the 21 days the policy allows, there is no credit recommendation. It also flags that the repayment record is 47 days old. Officer action: get the missing evidence and re-run." |
| ~2:40 | Panel 7: keep **OFF-001** and **APPROVE**, press **Record decision**; the release checks appear. | "The officer approves this request for more evidence. The release gate checks every condition and releases only to a sandbox. Nothing is sent to anyone." |
| ~2:50 | Panel 8: tool-call table with the two ❌ weather attempts. | "Every tool call is logged, including the two failed rainfall calls. The code, the evaluations and a full run with Qwen 7B are on GitHub." |

If the results appear before the waiting narration ends, stop and continue with the results. The total must stay under 3:00 without editing.

## What the video does not show

Approval invalidation after a change (EVALS E12), unauthorized release attempts (E11), the price-shock and drought cases, and the documented open failure (E04) are covered by the evaluation suite rather than the video. `./run_demo.sh --cli` prints a scripted terminal sequence that includes a blocked unauthorized release.
