/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        /* palette mirrors the CSS custom-property tokens in index.css */
        base: { DEFAULT: "#f7f1e3", 2: "#f2e9d5", 3: "#fdf9ef" },
        txt: { hi: "#232a3b", mid: "#4c5670", low: "#6a7186", faint: "#98a0b0" },
        acc: { DEFAULT: "#0f8f78", cyan: "#b8912f", violet: "#f2695c", magenta: "#d94fb8" },
        /* themed via CSS vars - each theme defines these in index.css */
        pos: "var(--accent-green)",
        neg: "var(--accent-red)",
        warn: "var(--accent-amber)",
      },
      fontFamily: {
        sans: [
          "-apple-system", "BlinkMacSystemFont", '"SF Pro Display"', '"SF Pro Text"',
          "Inter", '"Segoe UI"', "Roboto", '"Helvetica Neue"', "sans-serif",
        ],
      },
      boxShadow: {
        soft: "0 12px 32px rgba(122,92,34,0.12)",
        float: "0 18px 44px rgba(122,92,34,0.16)",
        glow: "0 10px 24px rgba(18,155,127,0.22)",
        glowp: "0 10px 24px rgba(242,105,92,0.18)",
        glows: "0 10px 24px rgba(184,145,47,0.20)",
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
