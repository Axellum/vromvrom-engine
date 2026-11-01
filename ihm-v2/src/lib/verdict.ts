/**
 * Phrase-verdict de l'Accueil, calculée à partir de `/api/guide/overview`.
 *
 * Fonction pure : mêmes données, même phrase. Elle ne dit « fonctionne » que si
 * le moteur a répondu, et n'affirme rien sur ce qui est inconnu (`null`).
 */
import type { GuideAlerte, GuideOverview } from '../api/guide';
import { ilYA, parseDate, pluriel } from './format';

export type TonVerdict = 'good' | 'warn' | 'bad';

export interface Verdict {
  ton: TonVerdict;
  phrase: string;
  critiques: number;
  attentions: number;
}

export function compterAlertes(alertes: GuideAlerte[] | null | undefined): { critiques: number; attentions: number } {
  const liste = alertes ?? [];
  return {
    critiques: liste.filter((a) => a.niveau === 'critique').length,
    attentions: liste.filter((a) => a.niveau === 'attention').length,
  };
}

export function construireVerdict(o: GuideOverview, maintenant: number = Date.now()): Verdict {
  const total = o.fournisseurs?.total ?? null;
  const utilisables = o.fournisseurs?.utilisables ?? null;
  const { critiques, attentions } = compterAlertes(o.alertes);
  const alertesConnues = Array.isArray(o.alertes);

  let debut: string;
  let ton: TonVerdict = 'good';
  if (total !== null && utilisables !== null) {
    if (utilisables === 0) {
      debut = `Le moteur est démarré, mais aucun fournisseur de modèles ne répond aux tests (0 sur ${total})`;
      ton = 'bad';
    } else {
      // Espace insécable avant « : » : sinon, en gros caractères sur téléphone, le deux-points part seul à la ligne.
      debut = `Le moteur fonctionne : ${utilisables} ${pluriel(utilisables, 'fournisseur')} sur ${total} ${
        utilisables > 1 ? 'répondent' : 'répond'
      } aux tests`;
    }
  } else {
    debut = 'Le moteur répond, mais l’état de ses fournisseurs est inconnu';
    ton = 'warn';
  }

  const dernier = parseDate(o.vocal?.dernier_echange);
  if (dernier) debut += `, dernière demande vocale ${ilYA(dernier, maintenant)}`;
  else if (o.vocal && o.vocal.echanges_7j === 0) debut += ', aucune demande vocale en 7 jours';

  let phrase = `${debut}.`;
  if (critiques > 0) phrase += ` ${critiques} ${pluriel(critiques, 'point')} critique${critiques > 1 ? 's' : ''}.`;
  if (attentions > 0) phrase += ` ${attentions} ${pluriel(attentions, 'point')} d’attention.`;
  if (alertesConnues && critiques === 0 && attentions === 0) phrase += ' Aucune alerte.';
  if (!alertesConnues) phrase += ' Les alertes ne sont pas disponibles.';

  if (critiques > 0) ton = 'bad';
  else if (attentions > 0 && ton === 'good') ton = 'warn';

  return { ton, phrase, critiques, attentions };
}
