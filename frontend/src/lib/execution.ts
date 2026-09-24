import type { Signal } from "./types";

/** Signals that were PUBLISHED but never entered on the broker, and why.
 *  The app must never let a paper/advisory signal look like a real position. */
const NOT_ENTERED_REASONS: Record<string, string> = {
  EXTRA_SIGNAL_NOT_ENTERED: "advisory",
  SKIPPED_STOP_TOO_TIGHT: "stop too tight",
  SKIPPED_SETUP_ALREADY_EXECUTED: "duplicate setup",
  FAILED: "order failed",
};

/** Returns the short reason a signal was not executed, or null if it is/was a
 *  real broker position (ticket present) or has no skip status. */
export function notEnteredReason(s: Signal): string | null {
  if ((s as any).mt5_ticket) return null;
  const st = (s as any).execution_status as string | undefined | null;
  if (!st) return null;
  return NOT_ENTERED_REASONS[st] ?? null;
}
