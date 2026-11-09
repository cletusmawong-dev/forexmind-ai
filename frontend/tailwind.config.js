/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        /* palette mirrors the CSS custom-property tokens in index.css */
        base: { DEFAULT: "#f7f1e3", 2: "#f2e9d5", 3: "#fdf9ef" },
        /* text + accent tokens are THEME-DRIVEN (rgb triplets keep alpha
           modifiers working). Before 2026-09-30 these were static dark hex -
           every text-txt-* string was invisible on the dark themes. */
        /* comma-form rgba: older mobile browsers cannot parse the modern
           "rgb(1 2 3 / a)" syntax -> text silently vanished on them
           (2026-10-01 tablet/sister reports). Triplets below are comma
           separated for exactly this reason. */
        txt: {
          hi: "rgba(var(--txt-hi-rgb), <alpha-value>)",
          mid: "rgba(var(--txt-mid-rgb), <alpha-value>)",
          low: "rgba(var(--txt-low-rgb), <alpha-value>)",
          faint: "rgba(var(--txt-faint-rgb), <alpha-value>)",
        },
        info: "rgb(var(--info-rgb), <alpha-value>)",
        acc: { DEFAULT: "var(--accent-green)", cyan: "var(--accent-cyan)",
               violet: "var(--accent-purple)", magenta: "var(--accent-magenta)" },
        /* themed via CSS vars - each theme defines these in index.css */
        pos: "var(--accent-green)",
        neg: "var(--accent-red)",
        warn: "var(--accent-amber)",
        accent: "var(--accent)",
        surface: {
          DEFAULT: "var(--bg-secondary)",
          raised: "var(--bg-elevated)",
          sunken: "var(--bg-primary)",
        },
        gain: "var(--accent-green)",
        loss: "var(--accent-red)",
      },
      fontSize: {
        display: ["32px", { lineHeight: "1.1", letterSpacing: "-0.03em" }],
        title: ["22px", { lineHeight: "1.2", letterSpacing: "-0.02em" }],
        body: ["14px", { lineHeight: "1.45" }],
        caption: ["12px", { lineHeight: "1.4" }],
        micro: ["10px", { lineHeight: "1.3", letterSpacing: "0.06em" }],
      },
      borderRadius: {
        card: "16px",
        control: "12px",
      },
      spacing: {
        18: "4.5rem",
        nav: "4.75rem",
      },
      fontFamily: {
        sans: [
          '"Space Grotesk"', "-apple-system", "BlinkMacSystemFont",
          '"SF Pro Display"', '"SF Pro Text"', "Inter", '"Segoe UI"', "Roboto",
          '"Helvetica Neue"', "sans-serif",
        ],
        mono: [
          '"JetBrains Mono"', "ui-monospace", "SFMono-Regular", "Menlo",
          "Consolas", "monospace",
        ],
      },
      boxShadow: {
        soft: "0 1px 0 rgba(255,255,255,0.04) inset, 0 12px 32px rgba(0,0,0,0.38)",
        float: "0 18px 48px rgba(0,0,0,0.5)",
        glow: "none",
        glowp: "none",
        glows: "none",
      },
      keyframes: {
        fadeUp: { from: { opacity: "0", transform: "translateY(10px)" }, to: { opacity: "1", transform: "translateY(0)" } },
        fadeIn: { from: { opacity: "0" }, to: { opacity: "1" } },
        pulseSoft: { "0%, 100%": { opacity: "0.55", transform: "scale(1)" }, "50%": { opacity: "0.9", transform: "scale(1.06)" } },
        breathe: { "0%, 100%": { transform: "scale(1)", opacity: "0.6" }, "50%": { transform: "scale(1.12)", opacity: "0.95" } },
        shimmerX: { from: { backgroundPosition: "200% 0" }, to: { backgroundPosition: "-200% 0" } },
        drift: { "0%, 100%": { transform: "translate(0,0) rotate(0deg)" }, "50%": { transform: "translate(8px,-10px) rotate(6deg)" } },
      },
      animation: {
        fadeUp: "fadeUp 0.5s cubic-bezier(0.22,1,0.36,1) both",
        fadeIn: "fadeIn 0.6s ease both",
        pulseSoft: "pulseSoft 3.2s ease-in-out infinite",
        breathe: "breathe 5s ease-in-out infinite",
        shimmerX: "shimmerX 2.6s linear infinite",
        drift: "drift 14s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
