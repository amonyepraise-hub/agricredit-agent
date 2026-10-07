# Three-minute demo script

Record the real system running; no slides. Before recording, run `./run_demo.sh` once so setup and tests finish. For the open-weight requirement, have Ollama serving `qwen2.5:7b-instruct` so the sidebar shows **Model: ollama:qwen2.5:7b-instruct**. If it shows the offline stub, say so on camera: the stub is not a language model.

| Time | Show | Required element |
|---|---|---|
| 0:00–0:15 | Dashboard open. Say: "Evidence-first underwriting agent for an extension officer. All household data is synthetic." Point to the model badge in the sidebar. | — |
| 0:15–0:25 | Sidebar: select **BEN-0421** (Maize Input Package B, 185,000 FCFA). Press **Run agent**. | 1. selecting a household |
| 0:25–0:45 | Panel 2: the `plan_investigation` line and the expanded plan (written by Qwen), then each MCP tool name next to its step. | 2. agent planning · 3. MCP tool calls |
| 0:45–1:00 | Panel 3: rainfall observation (−11% vs baseline), maize price at Glazoue, repayment records. Point out the **⚠ stale** marker on the 47-day-old repayment record. | 5. weather/market evidence |
| 1:00–1:20 | Panel 4: risk score, PD, capacity 1.35x base / 0.87x combined. The contribution chart answers "why is the risk what it is". | 4. risk calculation |
| 1:20–1:35 | The one-line verdict under the status bar summarises the result. Panel 5: capacity drops below the dashed 1.0x line under the combined shock. Panel 6: **REDUCE CREDIT AMOUNT → ~155,000 FCFA**, the "viable only under optimistic assumptions" warning, and the officer action naming the stale record. | 6. stress test · 7. recommendation |
| 1:35–2:05 | Sidebar: select **BEN-0008**, tick **Simulate rainfall-service outage**, Run. Panel 2 shows two failed weather attempts, the cache fallback marked stale, and then **REQUEST MORE EVIDENCE**: the cached observation is 25 days old, over the 21-day policy limit. No credit recommendation is made. | 8. deliberate data problem · 9. recovery/refusal |
| 2:05–2:35 | Back to **BEN-0421**, Run. Panel 7: choose **OFF-001**, APPROVE, Record decision. The release check table appears with every check passed, and the status becomes RELEASED (sandbox outbox, nothing sent). | 10. officer approval · 11. gated release |
| 2:35–2:55 | Panel 8: tool-call table (node, server, tool, attempt, latency, cited sources), then audit events (`awaiting_officer_approval`, `officer_approve`, `advisory_released`). Optionally show `logs/tool_calls.jsonl` in a terminal. | 12. audit log |
| 2:55–3:00 | "A draft never leaves without a named officer, and changing anything after approval voids it — see EVALS E11 and E12." | — |

Terminal alternative: `./run_demo.sh --cli` (or `make demo`) prints the same sequence, plus a blocked unauthorized release, in about 15 seconds with the stub. With Qwen on CPU it is slower; trim the recording accordingly.
