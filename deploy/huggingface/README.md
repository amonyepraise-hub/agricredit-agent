---
title: AgriCredit Agent
emoji: 🌾
colorFrom: green
colorTo: yellow
sdk: docker
app_port: 8501
pinned: false
short_description: Evidence-backed farm credit advice on real Benin data
---

# AgriCredit Agent: live demo

An AI agent that helps an extension officer in Benin decide whether a farming household can repay a
seed-and-fertiliser loan. It gathers evidence through MCP tools (ERA5 rainfall, WFP market prices,
official commune yields), stress-tests the loan, drafts a cited advice and waits for a named officer
to approve it.

**Sign in** with the demo account you were given. Pick a case (BEN-2001 Tanguiéta is a good start),
press **Run agent**, read the evidence and the draft, then approve or reject it as the officer.

Rainfall, prices, yields and input costs are real open data. The households are simulated inside
real communes, because real repayment records are private.

Code and documentation: https://github.com/amonyepraise-hub/agricredit-agent
