/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        base: { DEFAULT: "#0B0D12", 2: "#10131A", 3: "#161B26" },
        txt: { hi: "#F2F5FA", mid: "#A9B1C2", low: "#69748C", faint: "#454F66" },
        acc: { DEFAULT: "#6C9EFF", cyan: "#7CD5F2", violet: "#A79BF7" },
        pos: "#3ECF8E",
        neg: "#F0788C",
        warn: "#E5B567",
      },
      fontFamily: {
        sans: [
          "-apple-system", "BlinkMacSystemFont", '"SF Pro Display"', '"SF Pro Text"',
          "Inter", '"Segoe UI"', "Roboto", '"Helvetica Neue"', "sans-serif",
        ],
      },
      boxShadow: {
        soft: "0 12px 40px -14px rgba(0,0,0,0.55)",
        float: "0 20px 60px -16px rgba(0,0,0,0.7)",
        glow: "0 0 24px rgba(108,158,255,0.22)",
        glowp: "0 0 24px rgba(167,155,247,0.20)",
        glows: "0 0 18px rgba(62,207,142,0.30)",
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
