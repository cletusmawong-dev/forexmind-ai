export function Splash() {
  return (
    <div className="relative flex min-h-screen items-center justify-center">
      <div className="ambient" />
      <div className="noise" />
      <div className="flex flex-col items-center gap-7 animate-fadeIn">
        <div
          className="flex h-[74px] w-[74px] items-center justify-center rounded-[22px] text-[38px] font-black text-white"
          style={{
            background: "linear-gradient(150deg, #dcb84e 0%, #c39a33 55%, #a87f24 100%)",
            boxShadow: "0 2px 8px rgba(16,24,40,0.14)",
            letterSpacing: "-0.02em",
          }}
          aria-hidden="true"
        >
          F
        </div>
        <div className="text-center">
          <div className="text-[21px] font-extrabold tracking-tight text-[var(--text-primary)]">
            FOREXMIND <span className="font-black text-[var(--accent-gold)]">AI</span>
          </div>
          <div className="mt-1.5 text-[11.5px] font-normal tracking-wide text-txt-low">Your Personal AI Trading Research Agent</div>
        </div>
        <div className="h-[2px] w-28 overflow-hidden rounded-full bg-[rgba(var(--warm-rgb),0.07)]">
          <div className="h-full w-1/2 animate-shimmerX rounded-full bg-gradient-to-r from-transparent via-acc to-transparent bg-[length:200%_100%]" />
        </div>
      </div>
      <div className="absolute bottom-9 text-[9px] uppercase tracking-[0.28em] text-txt-faint">Trade - Learn - Grow</div>
    </div>
  );
}
