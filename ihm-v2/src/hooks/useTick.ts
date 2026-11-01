import { useEffect, useState } from "react";

/** Force un nouveau rendu à intervalle régulier (pour garder les durées relatives « il y a… » justes). */
export function useTick(intervalleMs = 60_000): number {
  const [n, setN] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => setN((x) => x + 1), intervalleMs);
    return () => window.clearInterval(id);
  }, [intervalleMs]);
  return n;
}
