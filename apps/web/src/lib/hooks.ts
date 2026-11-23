"use client";
import { useCallback, useEffect, useRef, useState } from "react";

import { api, ApiError, type Query } from "./api";

export type Loadable<T> = {
  data: T | undefined;
  error: ApiError | Error | undefined;
  loading: boolean;
  reload: () => void;
  setData: (d: T) => void;
};

/** GET `path` (null = don't fetch). `pollMs` re-fetches while `keepPolling(data)` is true. */
export function useApi<T>(path: string | null, opts: {
  query?: Query;
  pollMs?: number; keepPolling?: (d: T) => boolean;
} = {}): Loadable<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<ApiError | Error>();
  const [loading, setLoading] = useState<boolean>(path !== null);
  const [tick, setTick] = useState(0);
  const key = path === null ? null : path + JSON.stringify(opts.query ?? {});
  const optsRef = useRef(opts);
  optsRef.current = opts;

  useEffect(() => {
    if (key === null || path === null) { setLoading(false); return; }
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const run = async () => {
      setLoading(true);
      try {
        const d = await api<T>(path, { query: optsRef.current.query });
        if (!alive) return;
        setData(d); setError(undefined);
        const { pollMs, keepPolling } = optsRef.current;
        if (pollMs && keepPolling?.(d)) timer = setTimeout(run, pollMs);
      } catch (e) {
        if (alive) setError(e as Error);
      } finally {
        if (alive) setLoading(false);
      }
    };
    void run();
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, [key, tick, path]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload, setData };
}

/** Run a mutation with busy/error state. */
export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const run = useCallback(async <R,>(fn: () => Promise<R>): Promise<R | undefined> => {
    setBusy(true); setError(undefined);
    try { return await fn(); }
    catch (e) {
      const { errorText } = await import("./api");
      setError(errorText(e));
      return undefined;
    } finally { setBusy(false); }
  }, []);
  return { busy, error, setError, run };
}

export function useOnline(): boolean {
  const [online, setOnline] = useState(true);
  useEffect(() => {
    const up = () => setOnline(true);
    const down = () => setOnline(false);
    setOnline(navigator.onLine);
    window.addEventListener("online", up);
    window.addEventListener("offline", down);
    return () => { window.removeEventListener("online", up); window.removeEventListener("offline", down); };
  }, []);
  return online;
}
