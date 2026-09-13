export function Splash() {
  return (
    <div className="relative flex min-h-screen items-center justify-center">
      <div className="ambient" />
      <div className="noise" />
      <div className="flex flex-col items-center gap-7 animate-fadeIn">
        <div className="relative">
          <div
            className="absolute -inset-10 rounded-full blur-3xl opacity-40 animate-breathe"
            style={{ background: "conic-gradient(from 200deg, #6C9EFF, #A79BF7, #7CD5F2, #6C9EFF)" }}
          />
          <svg width="76" height="76" viewBox="0 0 48 48" fill="none">
            <defs>
              <linearGradient id="slg" x1="0" y1="0" x2="48" y2="48">
                <stop stopColor="#7CD5F2" />
                <stop offset="1" stopColor="#6C9EFF" />
              </linearGradient>
            </defs>
            <circle cx="24" cy="24" r="21" stroke="url(#slg)" strokeWidth="1.8" />
            <path
              d="M17 34v-4.6c-2.5-1.8-4-4.7-4-7.9C13 16 17.6 11.5 23.5 11.5c5.6 0 10.2 4 10.8 9.4l2.7 2.6-2.7 1v2.9c0 1.7-1.4 3.1-3.1 3.1H28V34"
              stroke="url(#slg)"
              strokeWidth="1.9"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            <path d="M17.5 26.5l4.6-4.6 3 3 5.4-5.4" stroke="#3ECF8E" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round" />
            <path d="M30.5 19.5h-4.4M30.5 19.5v4.4" stroke="#3ECF8E" strokeWidth="1.9" strokeLinecap="round" />
          </svg>
        </div>
        <div className="text-center">
          <div className="text-[21px] font-semibold tracking-tight text-txt-hi">
            ForexMind <span className="text-acc">AI</span>
          </div>
          <div className="mt-1.5 text-[11.5px] font-normal tracking-wide text-txt-low">Your Personal AI Trading Research Agent</div>
        </div>
        <div className="h-[2px] w-28 overflow-hidden rounded-full bg-white/[0.06]">
          <div className="h-full w-1/2 animate-shimmerX rounded-full bg-gradient-to-r from-transparent via-acc to-transparent bg-[length:200%_100%]" />
        </div>
      </div>
      <div className="absolute bottom-9 text-[9px] uppercase tracking-[0.28em] text-txt-faint">Trade · Learn · Grow</div>
    </div>
  );
}
