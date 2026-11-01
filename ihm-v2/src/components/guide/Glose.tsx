import { Fragment } from 'react';
import { Terme } from './Terme';
import { GLOSSAIRE } from '../../lib/glossaire';
import type { TermeId } from '../../lib/glossaire';

const MOTIF = /\[\[([a-z0-9-]+)(?:\|([^\]]+))?\]\]|\*\*(.+?)\*\*/g;

/**
 * Affiche un texte contenant des marqueurs `[[id]]` ou `[[id|libellé]]` en
 * remplaçant chacun par un `Terme` (définition en infobulle), et `**gras**`.
 * Un identifiant inconnu du glossaire est affiché en texte simple : jamais de
 * marqueur visible.
 */
export function Glose({ texte }: { texte: string }) {
  const morceaux: Array<string | { id: string; libelle: string } | { gras: string }> = [];
  let dernier = 0;
  for (const m of texte.matchAll(MOTIF)) {
    const debut = m.index ?? 0;
    if (debut > dernier) morceaux.push(texte.slice(dernier, debut));
    morceaux.push(m[3] !== undefined ? { gras: m[3] } : { id: m[1]!, libelle: m[2] ?? m[1]! });
    dernier = debut + m[0].length;
  }
  if (dernier < texte.length) morceaux.push(texte.slice(dernier));
  return (
    <>
      {morceaux.map((x, i) =>
        typeof x === 'string' ? (
          <Fragment key={i}>{x}</Fragment>
        ) : 'gras' in x ? (
          <strong key={i} className="font-semibold text-slate-200">{x.gras}</strong>
        ) : x.id in GLOSSAIRE ? (
          <Terme key={i} id={x.id as TermeId}>{x.libelle}</Terme>
        ) : (
          <Fragment key={i}>{x.libelle}</Fragment>
        ),
      )}
    </>
  );
}
