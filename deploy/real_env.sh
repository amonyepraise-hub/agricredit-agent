# Settings for the real-open-data mode, shared by the image build and the container start.
# Source this file; it expects APP_DIR to be set. Matches ./run_demo.sh --real.
export AGRICREDIT_POLICY_FILE=agent_policy_real.json
export AGRICREDIT_RISK_MODEL_PATH="$APP_DIR/data/risk_model_real.joblib"
export AGRICREDIT_MARKET_FRESHNESS_DAYS=90   # WFP prices are monthly and published with a lag
export AGRICREDIT_WEATHER_FRESHNESS_DAYS=14  # satellite rainfall arrives a few days late
export AGRICREDIT_TARGET_DEFAULT_RATE=0.094  # BCEAO: Benin microfinance portfolio degradation rate, Dec 2024
export AGRICREDIT_UPLIFT_REALISATION=1.0     # real packages carry a sourced yield gain (see opendata/sources.py)
AGRICREDIT_AS_OF=$(python -c "import json,sys; print(json.load(open(sys.argv[1]))['as_of'])" "$APP_DIR/data/real/processed/snapshot.json")
export AGRICREDIT_AS_OF
