/**
 * `Terme` : un mot technique avec sa définition en infobulle.
 *
 * S'ouvre au survol, au focus clavier et au toucher ; se ferme avec Échap, un
 * clic ailleurs, ou en quittant le mot. L'infobulle est positionnée dans la
 * fenêtre (jamais coupée au bord) ; les définitions viennent de `lib/glossaire.ts`.
 */
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { GLOSSAIRE } from '../../lib/glossaire';
import type { TermeDef, TermeId } from '../../lib/glossaire';

const LARGEUR_BULLE = 288;
const MARGE = 8;

interface Position {
  left: number;
  top?: number;
  bottom?: number;
}

export function Terme({ id, children }: { id: TermeId; children?: ReactNode }) {
  const def: TermeDef = GLOSSAIRE[id];
  const [ouvert, setOuvert] = useState(false);
  const [pos, setPos] = useState<Position | null>(null);
  const declencheur = useRef<HTMLButtonElement>(null);
  const idBulle = useId();

  const fermer = useCallback(() => setOuvert(false), []);

  const placer = useCallback(() => {
    const el = declencheur.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const largeur = Math.min(LARGEUR_BULLE, window.innerWidth - 2 * MARGE);
    const left = Math.max(MARGE, Math.min(r.left, window.innerWidth - largeur - MARGE));
    const placeEnBas = window.innerHeight - r.bottom;
    const placeEnHaut = r.top;
    // En dessous par défaut ; au-dessus si la place manque en bas et abonde en haut.
    if (placeEnBas < 180 && placeEnHaut > placeEnBas) {
      setPos({ left, bottom: window.innerHeight - r.top + 6 });
    } else {
      setPos({ left, top: r.bottom + 6 });
    }
  }, []);

  useLayoutEffect(() => {
    if (ouvert) placer();
  }, [ouvert, placer]);

  useEffect(() => {
    if (!ouvert) return;
    const surTouche = (e: KeyboardEvent) => {
      if (e.key === 'Escape') fermer();
    };
    const surPointeur = (e: PointerEvent) => {
      if (!declencheur.current?.contains(e.target as Node)) fermer();
    };
    window.addEventListener('keydown', surTouche);
    window.addEventListener('pointerdown', surPointeur);
    window.addEventListener('resize', fermer);
    // `capture` : le défilement de n'importe quel conteneur ferme l'infobulle.
    window.addEventListener('scroll', fermer, true);
    return () => {
      window.removeEventListener('keydown', surTouche);
      window.removeEventListener('pointerdown', surPointeur);
      window.removeEventListener('resize', fermer);
      window.removeEventListener('scroll', fermer, true);
    };
  }, [ouvert, fermer]);

  return (
    <>
      <button
        ref={declencheur}
        type="button"
        aria-describedby={ouvert ? idBulle : undefined}
        aria-expanded={ouvert}
        onClick={() => setOuvert((o) => !o)}
        onMouseEnter={() => setOuvert(true)}
        onMouseLeave={fermer}
        onFocus={() => setOuvert(true)}
        onBlur={fermer}
        className="inline cursor-help rounded-sm border-b border-dotted border-sky-400/70 bg-transparent p-0 text-left font-[inherit] text-inherit transition-colors hover:text-sky-300"
      >
        {children ?? def.terme}
      </button>
      {ouvert && pos && (
        <span
          id={idBulle}
          role="tooltip"
          style={{ left: pos.left, top: pos.top, bottom: pos.bottom, width: LARGEUR_BULLE }}
          className="pointer-events-none fixed z-[60] max-w-[calc(100vw-1rem)] rounded-lg border border-slate-700 bg-slate-900 p-3 text-left text-xs font-normal normal-case leading-relaxed tracking-normal text-slate-300 shadow-xl shadow-black/40"
        >
          <span className="mb-1 block font-semibold text-sky-300">{def.terme}</span>
          <span className="block">{def.definition}</span>
          {'exemple' in def && def.exemple && (
            <span className="mt-1.5 block text-slate-400">Exemple : {def.exemple}</span>
          )}
        </span>
      )}
    </>
  );
}
