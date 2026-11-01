/**
 * États d'une donnée chargée depuis le moteur — cinq cas distincts, jamais confondus :
 *  - chargement ;
 *  - requête suspendue (rien n'est revenu, sans erreur) ;
 *  - erreur (le moteur a répondu une erreur) ;
 *  - route absente (404 : ce moteur n'expose pas cette route) ;
 *  - vide (le moteur a répondu : il n'y a rien à montrer).
 * Aucune de ces situations n'affiche un chiffre à la place de la donnée.
 */
import type { ReactNode } from 'react';
import type { UseQueryResult } from '@tanstack/react-query';
import { Unplug } from 'lucide-react';
import { isRouteAbsente } from '../../api/helpers';
import {
  EmptyState, ErrorState, LoadingState, PausedState, queryPhase,
} from '../ui/primitives';

/** « Donnée non disponible : route /api/… absente sur ce moteur ». */
export function RouteAbsente({ route, compact = false }: { route: string; compact?: boolean }) {
  return (
    <div role="status" className="rounded-lg border border-slate-700 bg-slate-900/60 px-4 py-3">
      <div className="flex items-start gap-2.5">
        <Unplug className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" aria-hidden />
        <div className="min-w-0">
          <p className="text-sm font-medium text-slate-200">
            Donnée non disponible : route <code className="break-all font-mono text-xs text-sky-300">{route}</code>{' '}
            absente sur ce moteur
          </p>
          {!compact && (
            <p className="mt-1 text-xs leading-relaxed text-slate-500">
              Le moteur en service est sans doute plus ancien que cette interface. Aucun chiffre n’est affiché à la
              place : la page n’invente rien. La route apparaîtra après la mise à jour du moteur.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

interface DataGateProps<T> {
  query: UseQueryResult<T, unknown>;
  /** Route interrogée, pour le message « route absente ». */
  route: string;
  labelChargement?: string;
  /** Renvoie vrai quand la réponse ne contient rien à montrer. */
  estVide?: (data: T) => boolean;
  messageVide?: ReactNode;
  compact?: boolean;
  children: (data: T) => ReactNode;
}

export function DataGate<T>({
  query, route, labelChargement, estVide, messageVide, compact, children,
}: DataGateProps<T>) {
  const phase = queryPhase(query);
  if (phase === 'loading') return <LoadingState label={labelChargement} />;
  if (phase === 'paused') return <PausedState onRetry={() => void query.refetch()} />;
  if (phase === 'error') {
    if (isRouteAbsente(query.error)) return <RouteAbsente route={route} compact={compact} />;
    return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  }
  const data = query.data as T;
  if (estVide?.(data)) return <EmptyState>{messageVide ?? 'Le moteur n’a rien à montrer ici pour le moment.'}</EmptyState>;
  return <>{children(data)}</>;
}
