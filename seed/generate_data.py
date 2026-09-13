"""Generates the DEMO historical datasets (SPEC §59).

Realistic regime-switching price series per instrument (trend regimes,
volatility clustering, session-based activity) - clearly synthetic, stored
locally, and always presented as DEMO / HISTORICAL data. Never passed off as
live market data. Deterministic (seeded) so results are reproducible.

5 instruments x 180 days of 5-minute candles, from which 15M/1H/4H/1D are
derived by the provider.
"""
import numpy as np
import pandas as pd
import os

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend", "data", "demo")
DAYS = 180
BARS_PER_DAY = 288  # 24h x 12 x 5M

INSTRUMENTS = {
    #        anchor=realistic current level   annual vol   digits
    "XAUUSD": dict(anchor=3647.50, vol=0.16, digits=2),
    "NAS100": dict(anchor=21485.0, vol=0.22, digits=1),
    "EURUSD": dict(anchor=1.0842,  vol=0.08, digits=5),
    "GBPUSD": dict(anchor=1.2711,  vol=0.09, digits=5),
    "USDJPY": dict(anchor=151.86,  vol=0.10, digits=3),
}


def gen_symbol(name: str, cfg: dict, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = DAYS * BARS_PER_DAY
    # per-bar base sigma from annual vol
    bar_sigma = cfg["vol"] / np.sqrt(252 * BARS_PER_DAY)

    # Regime-switching drift (trend / range) with OU mean-reversion of the
    # log-price toward the anchor level -> bounded, realistic excursions.
    theta = 0.001                       # half-life ~ 690 5M bars (~2.4 days)
    drift_amp = 0.15 * theta            # equilibrium excursion ~ +/-16%
    log_anchor = np.log(cfg["anchor"])
    drift = np.zeros(n)
    i = 0
    state = rng.choice([-1.0, 0.0, 1.0], p=[0.3, 0.4, 0.3])
    while i < n:
        seg = int(rng.integers(150, 900))
        target = rng.choice([-1.0, -0.5, 0.0, 0.5, 1.0])  # symmetric: no net bias
        steps = np.linspace(state, target, min(seg, n - i))
        drift[i:i + len(steps)] = steps
        state = target
        i += len(steps)
    drift *= drift_amp

    # volatility clustering: slow multiplier + session activity
    t = np.arange(n)
    vol_mult = 0.7 + 0.6 * np.abs(np.sin(t / 2600.0) + 0.5 * np.sin(t / 700.0 + 1.3))
    hour_of_day = (t % BARS_PER_DAY) / 12.0  # hours
    session_mult = np.ones(n)
    lunch = ((hour_of_day >= 7.5) & (hour_of_day < 9)).astype(float) * 0.45
    asia = ((hour_of_day >= 0) & (hour_of_day < 6.5)).astype(float) * 0.55
    session_mult -= (lunch + asia)

    sigma = bar_sigma * vol_mult * session_mult
    noise = rng.standard_normal(n) * sigma
    log_p = np.empty(n)
    log_p[0] = log_anchor
    for t in range(1, n):
        log_p[t] = (log_p[t - 1] + theta * (log_anchor - log_p[t - 1])
                    + drift[t] + noise[t])
    close = np.exp(log_p)

    # build OHLC from close path with realistic wicks
    open_ = np.empty(n); open_[0] = close[0]
    open_[1:] = close[:-1]
    wick = np.abs(rng.standard_normal(n)) * sigma * close * 0.8
    high = np.maximum(open_, close) + wick
    low = np.minimum(open_, close) - np.abs(rng.standard_normal(n)) * sigma * close * 0.8
    volume = (rng.lognormal(mean=6.2, sigma=0.5, size=n) * session_mult).astype(int)

    # anchor the LAST close to the realistic current level (keeps shape,
    # guarantees plausible absolute prices - synthetic, clearly DEMO data)
    scale = cfg["anchor"] / close[-1]
    close *= scale; open_ *= scale; high *= scale; low *= scale
    d = cfg["digits"]
    close = np.round(close, d); open_ = np.round(open_, d)
    high = np.round(high, d); low = np.round(low, d)

    end = pd.Timestamp.utcnow().floor("5min") - pd.Timedelta(minutes=5)
    idx = pd.date_range(end=end, periods=n, freq="5min")
    df = pd.DataFrame({
        "timestamp": idx, "open": np.round(open_, cfg["digits"]),
        "high": np.round(high, cfg["digits"]), "low": np.round(low, cfg["digits"]),
        "close": np.round(close, cfg["digits"]), "volume": volume,
    })
    return df


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    for seed, (name, cfg) in enumerate(INSTRUMENTS.items(), start=101):
        df = gen_symbol(name, cfg, seed * 7919)
        path = os.path.join(OUT_DIR, f"{name}_5M.csv")
        df.to_csv(path, index=False)
        print(f"{name}: {len(df)} bars -> {path}  ({df['timestamp'].iloc[0]} .. {df['timestamp'].iloc[-1]})")


if __name__ == "__main__":
    main()
