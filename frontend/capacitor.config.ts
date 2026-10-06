import type { CapacitorConfig } from "@capacitor/cli";

/**
 * FOREXMIND AI - official Android app (final master build, section 39).
 * Native Capacitor wrapper around the EXISTING Vite app: one backend, one
 * trading engine, zero duplicated business logic (section 40). The shell
 * loads the production web bundle over HTTPS (App Links verified domain);
 * native additions: status-bar styling, splash, icon, hardware back button,
 * Telegram deep links open in the Telegram app, session lives in the
 * WebView cookie jar (secure, httpOnly - never in app storage).
 */
const config: CapacitorConfig = {
  appId: "ai.forexmind.app",
  appName: "FOREXMIND AI",
  webDir: "dist",
  android: {
    allowMixedContent: false,
    captureInput: true,
    webContentsDebuggingEnabled: false,
  },
  server: {
    // Production shell: the native WebView hosts the live app (always the
    // current build; the APK ships the same bundle as fallback).
    url: "https://forexmind-ai-v3.netlify.app",
    cleartext: false,
  },
  plugins: {
    StatusBar: {
      style: "DARK",
      backgroundColor: "#0b0d12",
      overlaysWebView: false,
    },
  },
};

export default config;
