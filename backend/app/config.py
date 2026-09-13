"""ForexMind AI backend configuration.

All secrets are read from environment variables. Nothing secret is ever
hard-coded or shipped to the frontend (SPEC §45).
"""
import os
from dataclasses import dataclass, field

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")


@dataclass
class Settings:
    app_name: str = "ForexMind AI"
    api_prefix: str = "/api"

    # --- Database -----------------------------------------------------------
    # Firestore activates when these env vars are provided. Until then the
    # Firestore-shaped LocalStore keeps the sandbox fully working.
    firebase_project_id: str = os.getenv("FIREBASE_PROJECT_ID", "")
    google_application_credentials: str = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
    firebase_credentials_json: str = os.getenv("FIREBASE_CREDENTIALS_JSON", "")

    # --- AI provider (XKiro) -------------------------------------------------
    # XKiro activates when XKIRO_API_KEY is provided. Until then the grounded
    # LocalAnalyst is used. The key NEVER leaves the server (SPEC §2, §45).
    xiro_api_key: str = os.getenv("XKIRO_API_KEY", "")
    xiro_base_url: str = os.getenv("XKIRO_BASE_URL", "https://api.xkiro.ai/v1")
    xiro_model: str = os.getenv("XKIRO_MODEL", "xkiro-1")

    # --- Market data ----------------------------------------------------------
    # "historical_demo" replays stored datasets; "live" fetches real market data
    # (Yahoo keyless, Twelve Data when TWELVEDATA_API_KEY is configured).
    market_data_provider: str = os.getenv("MARKET_DATA_PROVIDER", "historical_demo")
    twelvedata_api_key: str = os.getenv("TWELVEDATA_API_KEY", "")

    # --- Notifications ---------------------------------------------------------
    fcm_enabled: bool = bool(os.getenv("FCM_ENABLED", ""))

    # --- Auth -------------------------------------------------------------------
    jwt_secret: str = os.getenv("FOREXMIND_JWT_SECRET", "dev-only-secret-change-me")
    token_ttl_hours: int = 24 * 30

    # --- Replay engine ------------------------------------------------------------
    replay_interval_sec: float = float(os.getenv("REPLAY_INTERVAL_SEC", "4"))
    replay_enabled: bool = os.getenv("REPLAY_ENABLED", "1") != "0"

    # --- Learning -----------------------------------------------------------------
    min_trades_for_conclusion: int = 20       # SPEC §19/§20: no conclusions without evidence
    min_trades_for_experiment: int = 30       # below this -> INSUFFICIENT DATA (SPEC §23)
    min_trades_for_lesson: int = 20
    lesson_delta_pp: float = 8.0              # min win-rate deviation vs baseline (pp)


settings = Settings()

INITIAL_MARKETS = ["XAUUSD", "NAS100", "EURUSD", "GBPUSD", "USDJPY"]
TIMEFRAMES = ["5M", "15M", "1H", "4H", "1D"]
SESSIONS = {"Asian": (0, 8), "London": (8, 13), "NewYork": (13, 21), "Late": (21, 24)}


def session_of(dt_utc_hour: int) -> str:
    for name, (a, b) in SESSIONS.items():
        if a <= dt_utc_hour < b:
            return name
    return "Late"
