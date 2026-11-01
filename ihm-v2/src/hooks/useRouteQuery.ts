import { useQuery } from "@tanstack/react-query";
import { retryRaisonnable } from "../api/helpers";

interface Options {
  /** Rafraîchissement automatique, en millisecondes. */
  refetchInterval?: number;
  enabled?: boolean;
  staleTime?: number;
}

/**
 * Requête de lecture d'une route du moteur, avec une politique de nouvelle
 * tentative raisonnable (pas de relance sur un 404 ou un refus d'accès).
 */
export function useRouteQuery<T>(
  queryKey: readonly unknown[],
  queryFn: () => Promise<T>,
  options: Options = {},
) {
  return useQuery({
    queryKey,
    queryFn,
    retry: retryRaisonnable,
    staleTime: options.staleTime ?? 15_000,
    refetchInterval: options.refetchInterval,
    enabled: options.enabled,
  });
}
