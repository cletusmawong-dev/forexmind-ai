/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        /* palette mirrors the CSS custom-property tokens in index.css */
        base: { DEFAULT: "#070a12", 2: "#0b101d", 3: "#131a2e" },
        txt: { hi: "#eef3ff", mid: "#bcc9e6", low: "#8394b6", faint: "#5f6f92" },
        acc: { DEFAULT: "#4d7cfe", cyan: "#33d6f6", violet: "#8e7bff" },
        pos: "#2fd98a",
        neg: "#fb4d6a",
        warn: "#f5b84d",
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
        glow: "0 0 24px rgba(77,124,254,0.28)",
        glowp: "0 0 24px rgba(142,123,255,0.24)",
        glows: "0 0 18px rgba(47,217,138,0.32)",
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
