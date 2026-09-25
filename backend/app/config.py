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
    # Two-layer AI (master prompt SS6-SS8). IDs verified live on the Xkiro
    # /models list 2026-09-17; ALWAYS overridable via env - never hard-coded
    # in engine code.
    ai_primary_model: str = os.getenv("AI_PRIMARY_MODEL", "qwen/qwen3.7-plus:free")
    ai_escalation_model: str = os.getenv("AI_ESCALATION_MODEL", "openai/gpt-5.6-sol")
    ai_escalation_enabled: bool = os.getenv("AI_ESCALATION_ENABLED", "true").lower() != "false"
    ai_escalation_cooldown_s: int = int(os.getenv("AI_ESCALATION_COOLDOWN_S", "300"))
    # reasoning escalation models are slower + hungrier than the primary:
    # production logs showed GPT-5.6-Sol dying on the old hard 20s timeout
    ai_escalation_timeout_s: int = int(os.getenv("AI_ESCALATION_TIMEOUT_S", "45"))
    ai_escalation_max_tokens: int = int(os.getenv("AI_ESCALATION_MAX_TOKENS", "900"))

    # --- Market data ----------------------------------------------------------
    # "historical_demo" replays stored datasets; "live" fetches real market data
    # (Yahoo keyless, Twelve Data when TWELVEDATA_API_KEY is configured).
    market_data_provider: str = os.getenv("MARKET_DATA_PROVIDER", "historical_demo")
    twelvedata_api_key: str = os.getenv("TWELVEDATA_API_KEY", "")
    # Future tick-stream provider (VPS). Empty = no stream exists; the app
    # NEVER claims streaming capability while this is empty.
    market_data_stream_url: str = os.getenv("MARKET_DATA_STREAM_URL", "")
    market_data_stream_api_key: str = os.getenv("MARKET_DATA_STREAM_API_KEY", "")
    data_timezone: str = os.getenv("DATA_TIMEZONE", "UTC")
    # Optional broker-grade feed (free practice account at oanda.com).
    # Covers FX, gold and NAS100 CFDs; "practice" uses no real money.
    oanda_api_token: str = os.getenv("OANDA_API_TOKEN", "")
    oanda_env: str = os.getenv("OANDA_ENV", "practice")

    # --- Notifications ---------------------------------------------------------
    fcm_enabled: bool = bool(os.getenv("FCM_ENABLED", ""))
    # Telegram phone alerts: bot token from @BotFather + webhook secret.
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_bot_username: str = os.getenv("TELEGRAM_BOT_USERNAME", "")
    telegram_webhook_secret: str = os.getenv("TELEGRAM_WEBHOOK_SECRET", "fxm-hook")
    # Single-owner failsafe: alerts use this chat when a user has no linked chat.
    telegram_default_chat_id: str = os.getenv("TELEGRAM_DEFAULT_CHAT_ID", "")
    # The one user whose journal receives engine-executed trades (the bridge
    # drives a single Exness account). Everyone else's signals stay advisory.
    owner_user_id: str = os.getenv("FOREXMIND_OWNER_USER_ID", "cletusmawa")

    # --- MT5 execution (VPS bridge, demo account first) ----------------------
    # "off" = signals advisory only; "mt5_bridge" = auto-execute qualifying
    # signals through the user's Windows-VPS bridge next to the MT5 terminal.
    execution_mode: str = os.getenv("EXECUTION_MODE", "off")
    bridge_url: str = os.getenv("MT5_BRIDGE_URL", "")
    bridge_token: str = os.getenv("MT5_BRIDGE_TOKEN", "")
    # Kill switch default for users without an explicit setting. SAFE default:
    # blocked. EXECUTION_KILL_SWITCH=false pre-allows execution after the user
    # has finished VPS setup - flipping this env is a deliberate act.
    execution_kill_switch_default: bool = os.getenv("EXECUTION_KILL_SWITCH", "true").lower() != "false"
    execution_max_trades_per_day: int = int(os.getenv("EXECUTION_MAX_TRADES_PER_DAY", "6"))
    execution_risk_pct_cap: float = float(os.getenv("EXECUTION_RISK_PCT_CAP", "1.0"))
    execution_tp_level: int = int(os.getenv("EXECUTION_TP_LEVEL", "2"))   # take-profit level used on the order

    # --- Auth -------------------------------------------------------------------
    jwt_secret: str = os.getenv("FOREXMIND_JWT_SECRET", "dev-only-secret-change-me")
    token_ttl_hours: int = 24 * 30

    # --- Replay engine ------------------------------------------------------------
    replay_interval_sec: float = float(os.getenv("REPLAY_INTERVAL_SEC", "4"))
    replay_enabled: bool = os.getenv("REPLAY_ENABLED", "1") != "0"

    # --- Learning -----------------------------------------------------------------
    min_trades_for_conclusion: int = 20       # SPEC §19/§20: no conclusions without evidence
    min_trades_for_experiment: int = int(os.getenv("AUTOPSY_MIN_SAMPLE", "30"))
    # below this -> INSUFFICIENT DATA (SPEC §23 / autopsy spec §3; default 30)
    min_trades_for_lesson: int = int(os.getenv("FOREXMIND_LESSON_MIN_TRADES", "20"))
    lesson_delta_pp: float = 8.0              # min win-rate deviation vs baseline (pp)


settings = Settings()

# NAS100 off by default: the free TwelveData tier covers it only through the
# QQQ proxy, which tracks the index loosely - signals on thin data mislead.
# Set MARKETS_EXTRA=+NAS100 to re-enable when a real index feed is available.
_markets = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY"]
INITIAL_MARKETS = _markets + ([m.lstrip("+") for m in os.getenv("MARKETS_EXTRA", "").split(",") if m] if os.getenv("MARKETS_EXTRA") else [])
TIMEFRAMES = ["5M", "15M", "1H", "4H", "1D"]
SESSIONS = {"Asian": (0, 8), "London": (8, 13), "NewYork": (13, 21), "Late": (21, 24)}


def session_of(dt_utc_hour: int) -> str:
    for name, (a, b) in SESSIONS.items():
        if a <= dt_utc_hour < b:
            return name
    return "Late"
