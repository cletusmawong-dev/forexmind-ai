import logoMark from "../assets/logo-mark.png";

export function Splash() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-[#070b10] px-8">
      <img src={logoMark} alt="" className="h-20 w-20 animate-fadeIn rounded-[22px] object-cover" />
      <div className="mt-6 text-[22px] font-semibold tracking-[-0.03em] animate-fadeIn">ForexMind</div>
      <div className="mt-8 h-[3px] w-16 overflow-hidden rounded-full bg-white/10">
        <div className="h-full w-1/2 animate-shimmerX rounded-full bg-[var(--accent)]" />
      </div>
    </div>
  );
}
