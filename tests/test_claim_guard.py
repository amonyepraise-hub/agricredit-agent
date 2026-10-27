"""The narrative claim guard: the model's conclusions must agree with the deterministic key findings."""
from agricredit.agent.graph import unsupported_claims

# Sentences from a real qwen2.5:1.5b draft on real Benin data (case in Tanguieta, 27 Oct 2026).
QWEN_DRAFT = """4. Despite the low expected yield of 1242.64 kg/ha, the high expected repayment capacity ratio of 0.87x indicates that the household has the potential to repay the loan.
5. The simulated household in Tanguieta commune has a yield of 1,161 kg/ha for maize.
7. The rainfall for the 2026 season-to-date is 546.1 mm, which is 19.1% below the baseline, suggesting a drought condition.
8. The independent rainfall cross-check confirms the drought condition."""
FINDINGS = {"drought": False, "sources_agree": False, "can_repay": False, "commune_yields": {"1161"}}


def test_guard_catches_each_contradiction_in_a_real_small_model_draft():
    problems = unsupported_claims(QWEN_DRAFT, FINDINGS)
    assert len(problems) == 4


def test_guard_accepts_a_faithful_explanation():
    ok = ("Rainfall was 19.1% below its baseline, which does not reach the -20% drought threshold. "
          "The independent source does not confirm this reading; the two sources disagree. "
          "Expected cash does not cover the loan, so the household is not able to repay it in full. "
          "The official commune average maize yield is 1,161 kg/ha.")
    assert unsupported_claims(ok, FINDINGS) == []


def test_guard_is_silent_when_findings_allow_the_claim():
    assert unsupported_claims("Drought this season cut yields.", {"drought": True}) == []
