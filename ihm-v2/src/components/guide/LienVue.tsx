import type { ReactNode } from 'react';
import { ArrowRight } from 'lucide-react';
import { hashForView } from '../../lib/navigation';
import type { ViewKey } from '../../lib/navigation';

/**
 * Lien vers une autre vue : un vrai lien (`#/comprendre`), donc ouvrable dans un
 * nouvel onglet ; le changement de vue passe par l'écoute de l'adresse (uiStore).
 */
export function LienVue({
  vue,
  children,
  className = '',
  fleche = true,
}: {
  vue: ViewKey;
  children: ReactNode;
  className?: string;
  fleche?: boolean;
}) {
  return (
    <a
      href={hashForView(vue)}
      className={`inline-flex items-center gap-1 font-medium text-sky-400 transition-colors hover:text-sky-300 ${className}`}
    >
      {children}
      {fleche && <ArrowRight className="h-3.5 w-3.5 shrink-0" aria-hidden />}
    </a>
  );
}
