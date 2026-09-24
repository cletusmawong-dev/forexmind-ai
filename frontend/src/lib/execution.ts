import type { Signal } from "./types";

/** Signals that were PUBLISHED but never entered on the broker, and why.
 *  The app must never let a paper/advisory signal look like a real position. */
const NOT_ENTERED_REASONS: Record<string, string> = {
  EXTRA_SIGNAL_NOT_ENTERED: "advisory",
  SKIPPED_STOP_TOO_TIGHT: "stop too tight",
  SKIPPED_SETUP_ALREADY_EXECUTED: "duplicate setup",
  SKIPPED_NOT_ENGINE_OWNER: "other account",
  SKIPPED_EXEC_DAILY_CAP: "daily cap",
  SKIPPED_BRIDGE_OFFLINE: "bridge offline",
  SKIPPED_KILL_SWITCH: "kill switch",
  SKIPPED_ADVISORY_MODE: "advisory mode",
  FAILED: "order failed",
};

/** Returns the short reason a signal was not executed, or null if it is/was a
 *  real broker position (ticket present) or has no skip status. */
export function notEnteredReason(s: Signal): string | null {
  if ((s as any).mt5_ticket) return null;
  const st = (s as any).execution_status as string | undefined | null;
  if (!st) return null;
  if (st.startsWith("SKIPPED_") && !(st in NOT_ENTERED_REASONS)) return "not entered";
  return NOT_ENTERED_REASONS[st] ?? null;
}
