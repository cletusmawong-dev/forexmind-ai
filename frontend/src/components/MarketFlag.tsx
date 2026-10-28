/**
 * MarketFlag - reference-style circular market icon (approved Signals ref).
 * Minimal SVG flags for the majors, gold ingots for XAUUSD, neutral ticker
 * disc fallback. No emoji - crisp vector art that renders identically everywhere.
 */
export function MarketFlag({ market, size = 38 }: { market: string; size?: number }) {
  const m = (market || "").toUpperCase();
  const common = {
    width: size,
    height: size,
    viewBox: "0 0 40 40",
    className: "shrink-0",
    "aria-hidden": true as const,
  };

  if (m === "XAUUSD" || m === "XAGUSD") {
    const silver = m === "XAGUSD";
    return (
      <svg {...common}>
        <circle cx="20" cy="20" r="19" fill={silver ? "#eef0f4" : "#fdf6e3"} stroke="#e7e9ee" />
        {[
          [9, 22], [20, 22], [31, 22],
        ].map(([x, y], i) => (
          <g key={i}>
            <rect x={x} y={y} width={11} height={6.5} rx={1.2} fill={silver ? "#b7bec9" : "#d4a938"} />
            <rect x={x + 1.5} y={y - 5} width={8} height={5} rx={1} fill={silver ? "#d3d9e0" : "#e8c25c"} />
          </g>
        ))}
      </svg>
    );
  }
  if (m === "NAS100" || m === "USTEC" || m === "US100") {
    return (
      <svg {...common}>
        <circle cx="20" cy="20" r="19" fill="#101828" />
        <path d="M10 26l6-8 5 5 7-11" fill="none" stroke="#3b82f6" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx="28" cy="12" r="2" fill="#22d3ee" />
      </svg>
    );
  }
  if (m === "EURUSD") {
    return (
      <svg {...common}>
        <circle cx="20" cy="20" r="19" fill="#1e4bd8" />
        {Array.from({ length: 10 }).map((_, i) => {
          const a = (i / 10) * Math.PI * 2 - Math.PI / 2;
          return <circle key={i} cx={20 + Math.cos(a) * 8.5} cy={20 + Math.sin(a) * 8.5} r="1.6" fill="#ffd23e" />;
        })}
      </svg>
    );
  }
  if (m === "GBPUSD") {
    return (
      <svg {...common}>
        <clipPath id="gbpc"><circle cx="20" cy="20" r="19" /></clipPath>
        <g clipPath="url(#gbpc)">
          <rect width="40" height="40" fill="#1e3a7b" />
          <path d="M0 0l40 40M40 0L0 40" stroke="#fff" strokeWidth="7" />
          <path d="M0 0l40 40M40 0L0 40" stroke="#d0313d" strokeWidth="3" />
          <path d="M20 0v40M0 20h40" stroke="#fff" strokeWidth="10" />
          <path d="M20 0v40M0 20h40" stroke="#d0313d" strokeWidth="5.5" />
        </g>
        <circle cx="20" cy="20" r="19" fill="none" stroke="#e7e9ee" />
      </svg>
    );
  }
  if (m === "USDJPY") {
    return (
      <svg {...common}>
        <circle cx="20" cy="20" r="19" fill="#f6f7f9" stroke="#e7e9ee" />
        <circle cx="20" cy="20" r="8" fill="#d0313d" />
      </svg>
    );
  }
  if (m === "USDCHF" || m === "USDCAD" || m === "AUDUSD" || m === "NZDUSD" || m.startsWith("USD") || m.endsWith("USD") || m.includes("USD")) {
    // generic USD-backed pair: USD canton + neutral field (keeps majors recognizable)
    return (
      <svg {...common}>
        <clipPath id="usdc"><circle cx="20" cy="20" r="19" /></clipPath>
        <g clipPath="url(#usdc)">
          <rect width="40" height="20" fill="#3b82f6" />
          <rect y="20" width="40" height="20" fill="#eef0f4" />
          <rect width="18" height="14" fill="#173d8f" />
          {[3, 7, 11].map((y) => <rect key={y} x="3" y={y} width="12" height="1.6" fill="#fff" opacity="0.9" />)}
        </g>
        <circle cx="20" cy="20" r="19" fill="none" stroke="#e7e9ee" />
      </svg>
    );
  }
  // fallback: ticker disc
  return (
    <svg {...common}>
      <circle cx="20" cy="20" r="19" fill="#f2f4f7" stroke="#e7e9ee" />
      <text x="20" y="25" textAnchor="middle" fontSize="10" fontWeight="800" fill="#3f4756">
        {m.slice(0, 3)}
      </text>
    </svg>
  );
}
