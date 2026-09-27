import { useCallback, useState } from "react";

export type ActionState = "idle" | "loading" | "success" | "error";

/** P16 UI-state reliability: ONE shared pattern for every user-triggered
 *  action - loading while in flight, success message, real failure reason.
 *  Never silent, never optimistic: data refresh is the caller's job. */
export function useAsyncAction() {
  const [state, setState] = useState<ActionState>("idle");
  const [message, setMessage] = useState<string | null>(null);

  const run = useCallback(async (fn: () => Promise<any>, okMsg?: string) => {
    setState("loading");
    setMessage(null);
    try {
      const res = await fn();
      setState("success");
      setMessage(okMsg ?? "Done");
      return res;
    } catch (e: any) {
      setState("error");
      setMessage(e?.message || "Something went wrong");
      throw e;
    }
  }, []);

  const reset = useCallback(() => { setState("idle"); setMessage(null); }, []);
  return { state, message, run, reset,
           busy: state === "loading" };
}
