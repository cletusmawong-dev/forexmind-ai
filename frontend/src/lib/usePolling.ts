import { useEffect, useRef, useState } from "react";

export function usePolling<T>(fn: () => Promise<T>, intervalMs: number, deps: any[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [ctr, setCtr] = useState(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    const tick = async () => {
      try {
        const d = await fnRef.current();
        if (alive) {
          setData(d);
          setError(null);
        }
      } catch (e: any) {
        if (alive && e?.status !== 401) setError(e?.message || "error");
      } finally {
        if (alive) {
          setLoading(false);
          timer = window.setTimeout(tick, intervalMs);
        }
      }
    };
    tick();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, ctr]);

  return { data, error, loading, setData, refresh: () => setCtr((c) => c + 1) };
}
