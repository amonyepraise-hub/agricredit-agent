"""EHCVM 2021/22 loader, tested on SMALL MOCK Stata files in the survey's layout (no real survey data in the repo)."""
import numpy as np
import pandas as pd
import pytest

from agricredit.opendata import ehcvm


@pytest.fixture()
def mock_raw(tmp_path):
    rng = np.random.default_rng(0)
    n = 120
    hh = pd.DataFrame({"grappe": np.repeat(np.arange(1, 13), 10), "menage": np.tile(np.arange(1, 11), 12)})
    welfare = hh.assign(departement=np.where(hh.grappe <= 6, 1, 2), hhweight=rng.uniform(50, 150, n))
    welfare.to_stata(tmp_path / "ehcvm_welfare_ben2021.dta", write_index=False,
                     value_labels={"departement": {1: "Borgou", 2: "Zou"}})
    plots = pd.concat([hh.assign(s16aq02=1, s16aq03=1), hh.assign(s16aq02=2, s16aq03=1)], ignore_index=True)
    plots["s16aq08"] = rng.choice([1, 2, 3], len(plots), p=[0.6, 0.25, 0.15])            # maize / rice / yam
    plots["s16aq09b"] = rng.choice([1, 2, 3], len(plots), p=[0.7, 0.2, 0.1])             # ha / m2 / local unit
    plots["s16aq09a"] = np.where(plots.s16aq09b == 1, rng.uniform(0.3, 3, len(plots)), rng.uniform(2000, 20000, len(plots)))
    plots.to_stata(tmp_path / "s16a_me_ben2021.dta", write_index=False, value_labels={
        "s16aq08": {1: "Maïs", 2: "Riz paddy", 3: "Igname"},
        "s16aq09b": {1: "Hectare", 2: "Mètre carré", 3: "Corde (unité locale)"}})
    return tmp_path


def test_profile_has_weighted_department_aggregates_only(mock_raw, tmp_path):
    out = tmp_path / "profile.csv"
    prof = ehcvm.build_profile(mock_raw, out, log=lambda m: None)
    assert set(prof["department"]) == {"Benin (national)", "Borgou", "Zou"}
    maize = prof[(prof.department == "Borgou") & (prof.crop == "maize")].iloc[0]
    assert 0 < maize["share_growing"] <= 1 and maize["area_p10"] <= maize["area_p50"] <= maize["area_p90"]
    assert maize["area_p90"] < 6  # square metres were converted to hectares; local units were dropped
    assert "grappe" not in prof.columns and "menage" not in prof.columns  # nothing household-level is written
    assert (prof["n_households"] >= ehcvm.MIN_CELL).all()


def test_sample_area_stays_within_the_stored_quantiles():
    row = {"area_p10": 0.4, "area_p25": 0.7, "area_p50": 1.1, "area_p75": 1.8, "area_p90": 3.0}
    draws = [ehcvm.sample_area(row, np.random.default_rng(i)) for i in range(200)]
    assert min(draws) >= 0.4 and max(draws) <= 3.0 and 0.8 < float(np.median(draws)) < 1.5


def test_missing_files_give_a_clear_message(tmp_path):
    with pytest.raises(SystemExit, match="Missing EHCVM files"):
        ehcvm.build_profile(tmp_path, tmp_path / "x.csv", log=lambda m: None)
