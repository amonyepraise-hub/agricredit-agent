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


def test_guard_reads_french():
    bad = ("Les précipitations sont inférieures de 19,1 % à la normale, ce qui indique une sécheresse. "
           "La vérification indépendante confirme cette sécheresse. Le ménage est capable de rembourser le prêt. "
           "Le ménage a un rendement de 1 161 kg/ha pour le maïs.")
    assert len(unsupported_claims(bad, FINDINGS)) == 4
    good = ("Les précipitations sont inférieures de 19,1 % à la normale, sans atteindre le seuil de sécheresse de -20 %. "
            "La source indépendante ne confirme pas ce chiffre. Le ménage n'est pas en mesure de rembourser le prêt en totalité. "
            "Le rendement moyen officiel de la commune est de 1 161 kg/ha.")
    assert unsupported_claims(good, FINDINGS) == []


def test_number_guard_reads_french_number_formats():
    from agricredit.agent.llm import _numbers_in
    assert _numbers_in("0,87 fois ; 98 000 FCFA ; 1 242,64 kg/ha") == [0.87, 98000.0, 1242.64]
    assert _numbers_in("98,000 FCFA and 1,242.64 kg") == [98000.0, 1242.64]


def test_signed_figures_in_the_evidence_may_be_restated_without_sign():
    # Regression: a real qwen2.5:7b French draft (BEN-2001, 28 Oct 2026) was rejected because the warning said
    # "-45.3%" and the model wrote "en baisse de 45,3 %".
    from agricredit.agent.llm import unsupported_numbers
    facts = {"warnings": ["latest maize price is -45.3% vs the usual September price (2021-2025 median)"],
             "rain": "Rainfall 546.1 mm (−19.1% vs 675 mm baseline)"}
    assert unsupported_numbers("Le prix du maïs est en baisse de 45,3 % ; pluie inférieure de 19,1 %.", facts) == []
    assert unsupported_numbers("Le prix du maïs est en baisse de -45,3%.", facts) == []
    assert unsupported_numbers("Le prix du maïs est en baisse de 52,8 %.", facts) == [52.8]
