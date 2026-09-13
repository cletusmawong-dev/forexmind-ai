export function Logo({ size = 34, withText = true, subtitle }: { size?: number; withText?: boolean; subtitle?: string }) {
  return (
    <div className="flex items-center gap-2.5">
      <svg width={size} height={size} viewBox="0 0 48 48" fill="none">
        <defs>
          <linearGradient id="lmg" x1="0" y1="0" x2="48" y2="48">
            <stop stopColor="#2dd4bf" />
            <stop offset="1" stopColor="#3b82f6" />
          </linearGradient>
        </defs>
        <circle cx="24" cy="24" r="21" stroke="url(#lmg)" strokeWidth="2.4" />
        {/* head silhouette */}
        <path
          d="M17 34v-4.6c-2.5-1.8-4-4.7-4-7.9C13 16 17.6 11.5 23.5 11.5c5.6 0 10.2 4 10.8 9.4l2.7 2.6-2.7 1v2.9c0 1.7-1.4 3.1-3.1 3.1H28V34"
          stroke="url(#lmg)"
          strokeWidth="2.2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        {/* rising arrow */}
        <path
          d="M17.5 26.5l4.6-4.6 3 3 5.4-5.4"
          stroke="#10b981"
          strokeWidth="2.4"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <path d="M30.5 19.5h-4.4M30.5 19.5v4.4" stroke="#10b981" strokeWidth="2.2" strokeLinecap="round" />
      </svg>
      {withText && (
        <div className="leading-tight">
          <div className="text-[17px] font-extrabold tracking-tight">
            ForexMind <span className="text-transparent bg-clip-text bg-gradient-to-r from-neon-green to-neon-teal">AI</span>
          </div>
          {subtitle && <div className="text-[10px] text-slate-400 -mt-0.5">{subtitle}</div>}
        </div>
      )}
    </div>
  );
}
