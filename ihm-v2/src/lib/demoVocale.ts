/**
 * Contenu de la page « Démo vocale & maison ».
 *
 * Source : guide `docs/guide/03_demo_ecran_moteur_ha.md`, état lu le 01/11/2026 en
 * lecture seule (firmware et paquets Tab5, composant Home Assistant, code et
 * configuration du moteur, pipelines Assist, audit vocal du Deck). Chaque durée
 * porte sa date de mesure ; ce qui n'a pas pu être vérifié est dit « non mesurée »
 * ou « non vérifié ». Les textes acceptent les marqueurs `[[terme|libellé]]`
 * (rendus par `Glose`).
 */
import type { LucideIcon } from 'lucide-react';
import {
  AudioLines, Brain, House, Mic, Plug, Server, Speaker, Wifi,
} from 'lucide-react';

export const DATE_MESURE = '01/11/2026';

/* ── Le trajet d'une phrase ──────────────────────────────────────────────── */

/** Ce que le moteur peut voir de ce maillon. */
export type Visibilite = 'direct-moteur' | 'direct-ha' | 'invisible';

export interface EtapeTrajet {
  numero: number;
  titre: string;
  lieu: string;
  icone: LucideIcon;
  resume: string;
  duree: string;
  visibilite: Visibilite;
}

export const ETAPES_TRAJET: EtapeTrajet[] = [
  {
    numero: 1, titre: '« Okay Nabu »', lieu: 'Sur l’écran Tab5', icone: Mic, visibilite: 'invisible',
    resume: 'L’écran reconnaît lui-même le [[mot-de-reveil|mot de réveil]] : aucun son ne part vers Internet tant qu’il n’a pas été entendu.',
    duree: 'Non mesurée',
  },
  {
    numero: 2, titre: 'Envoi de l’audio', lieu: 'Écran → Home Assistant (Wi-Fi)', icone: Wifi, visibilite: 'invisible',
    resume: 'L’écran ouvre une session avec [[ha|Home Assistant]] et lui envoie la voix (16 kHz, une seule voie). Il doit être connecté à Home Assistant pour que le mot de réveil lance quoi que ce soit.',
    duree: 'Incluse dans l’étape suivante',
  },
  {
    numero: 3, titre: 'Reconnaissance vocale', lieu: 'Home Assistant (cloud)', icone: AudioLines, visibilite: 'invisible',
    resume: 'Le [[pipeline-assist|pipeline Assist]] transforme la voix en texte ([[stt|STT]], français). Le texte reconnu s’affiche dans la fenêtre de l’écran. C’est le maillon le plus fragile.',
    duree: '2,7 à 4,9 s, médiane 3,7 s (9 passages « Domotique », temps de parole compris)',
  },
  {
    numero: 4, titre: 'Agent de conversation', lieu: 'Home Assistant (VM)', icone: Plug, visibilite: 'direct-ha',
    resume: 'Le composant « moteur_ia_conversation » ([[agent-de-conversation|agent de conversation]]) envoie le texte au moteur avec un mode : « ha » (Domotique) ou « chat » (Discussion). Délais : 30 s en Domotique, 35 s en Discussion.',
    duree: 'Incluse dans l’étape suivante',
  },
  {
    numero: 5, titre: 'Moteur IA : routage', lieu: 'Moteur (Steam Deck)', icone: Server, visibilite: 'direct-moteur',
    resume: 'Le moteur essaie ses raccourcis dans un ordre fixe (commande, état, heure et météo), puis seulement un modèle de langage. Le Planner et les agents de code ne sont jamais atteints par la voix.',
    duree: 'Médiane 0,4 s en Domotique, 0,7 s en Discussion, de 0,04 à 4,6 s (vue de Home Assistant)',
  },
  {
    numero: 6, titre: 'Maison ou modèle', lieu: 'Home Assistant ou fournisseur de modèles', icone: House, visibilite: 'direct-moteur',
    resume: 'Un ordre part vers la maison (appel de service), une question lit un état, une discussion interroge la [[cascade]] de modèles. Les ordres agissent sur la vraie maison.',
    duree: 'Commande 0,1 à 0,2 s · état 0,06 à 0,14 s · heure et météo moins de 40 ms · discussion 3,0 s en médiane (8,0 s au plus)',
  },
  {
    numero: 7, titre: 'Synthèse vocale', lieu: 'Home Assistant', icone: Brain, visibilite: 'invisible',
    resume: 'La réponse écrite devient une voix ([[tts|TTS]]). En Domotique, des phrases déjà enregistrées sont jouées ; sinon une voix de secours prend le relais. En Discussion, c’est la voix du cloud de Home Assistant.',
    duree: 'Non mesurée (Home Assistant diffuse l’audio pendant qu’il se génère)',
  },
  {
    numero: 8, titre: 'Lecture sur l’écran', lieu: 'Sur l’écran Tab5', icone: Speaker, visibilite: 'invisible',
    resume: 'L’écran joue la voix et affiche le texte dit dans sa fenêtre, puis se remet à écouter le mot de réveil. En Discussion, il doit réécouter sans nouveau « Okay Nabu » (non revérifié à l’écran).',
    duree: 'Durée de la phrase',
  },
];

/** Durées mesurées, pour la comparaison visuelle. `barre` en secondes (échelle 0 à 10 s). */
export interface MesureDuree {
  libelle: string;
  detail: string;
  barre: number;
  valeur: string;
}

export const MESURES_DUREE: MesureDuree[] = [
  { libelle: 'Passage complet, Domotique', detail: 'de « run-start » à « run-end » dans Home Assistant ; 10 passages du 29/10 au 01/11 ; de 3,0 à 9,4 s', barre: 4.3, valeur: 'médiane 4,3 s' },
  { libelle: 'Passage complet, Discussion', detail: '10 passages du 01/11', barre: 5.7, valeur: 'médiane 5,7 s' },
  { libelle: 'dont reconnaissance vocale', detail: '9 passages « Domotique » ; de 2,7 à 4,9 s ; temps de parole compris', barre: 3.7, valeur: 'médiane 3,7 s' },
  { libelle: 'Appel du moteur, Domotique', detail: 'vu de Home Assistant ; 9 passages', barre: 0.4, valeur: 'médiane 0,4 s' },
  { libelle: 'Appel du moteur, Discussion', detail: 'vu de Home Assistant ; 4 passages', barre: 0.7, valeur: 'médiane 0,7 s' },
  { libelle: 'Côté moteur : une commande', detail: 'journal d’audit du Deck, 200 dernières lignes du 19/10 au 01/11', barre: 0.196, valeur: '110 à 196 ms' },
  { libelle: 'Côté moteur : un modèle de langage', detail: 'même journal ; maximum observé 8,0 s', barre: 3.0, valeur: 'médiane 3,0 s' },
];

/* ── Les trois chemins ───────────────────────────────────────────────────── */

export interface CheminDemo {
  cle: 'commande' | 'question' | 'discussion';
  lettre: string;
  titre: string;
  exemples: string[];
  fonctionnement: string;
  garde: string;
  duree: string;
}

export const CHEMINS_DEMO: CheminDemo[] = [
  {
    cle: 'commande', lettre: 'A', titre: 'Commande de la maison',
    exemples: ['Allume la lumière du salon.', 'Ferme le volet de la serre.', 'Éteins la clim du salon.'],
    fonctionnement: 'Correspondance exacte, puis approchée, avec le catalogue de commandes et des mots-clés de pièces. Aucun [[llm|modèle de langage]] n’intervient. Une panne de Home Assistant est annoncée telle quelle plutôt qu’excusée.',
    garde: 'Avant d’envoyer l’ordre, le moteur vérifie que l’appareil n’est pas indisponible.',
    duree: '110 à 196 ms côté moteur',
  },
  {
    cle: 'question', lettre: 'B', titre: 'Question sur la maison',
    exemples: ['Quelle température fait-il dans le salon ?', 'Quelle heure est-il ?', 'Quel temps fait-il demain ?'],
    fonctionnement: 'Lectures déterministes de l’état de Home Assistant, de l’entité météo et de l’horloge. Depuis le 20/10, l’agent Domotique répond aussi à l’heure, à la date et à la météo.',
    garde: 'Une question n’est pas un ordre : le test porte sur le texte brut reconnu. Le garde-fou est dans le code ; son comportement en conditions réelles n’a pas été rejoué : non vérifié.',
    duree: '4 à 143 ms côté moteur',
  },
  {
    cle: 'discussion', lettre: 'C', titre: 'Discussion libre avec un modèle',
    exemples: ['Raconte-moi une blague.', 'Explique-moi la différence entre un volet et un store.'],
    fonctionnement: 'Le moteur essaie, dans l’ordre, la liste de modèles de sa politique de routage : le premier qui répond gagne. Réponse limitée à 3 phrases, sans mise en forme. Il garde les 5 derniers tours de la conversation. Il ne peut pas commander la maison (outils en lecture seule). L’agenda et le web passent par des spécialistes.',
    garde: 'Au bout de 20 s, le moteur répond « Désolé, je n’ai pas su répondre. Peux-tu reformuler ? ».',
    duree: 'Médiane 3,0 s, maximum observé 8,0 s',
  },
];

/* ── Je veux… → où → comment ─────────────────────────────────────────────── */

export interface Reglage {
  veux: string;
  ou: string;
  comment: string;
  effet: string;
}

export const REGLAGES: Reglage[] = [
  { veux: 'Changer la langue de l’écran', ou: 'Home Assistant : select.tab5_language (page de l’appareil « Tab5 »)', comment: 'Choisir parmi Français, English, Deutsch, Nederlands, Español, Italiano, Türkçe.', effet: 'L’écran redémarre. Seuls ses textes changent, pas la voix.' },
  { veux: 'Changer la langue de la voix', ou: 'Home Assistant : Paramètres → Assistants vocaux → le pipeline', comment: 'Modifier ensemble les trois langues (langue, reconnaissance vocale, synthèse vocale).', effet: 'Le moteur répond en français : pour parler anglais, utiliser un pipeline Home Assistant.' },
  { veux: 'Choisir qui répond (Domo ou Discu)', ou: 'Écran : boutons Domo et Discu. Home Assistant : assistant préféré = Domo ; select.tab5_pipeline_de_discussion = Discu', comment: 'Ne pas régler select.tab5_assistant à la main : il est remis à chaque appui et à chaque démarrage.', effet: 'Domo : pipeline « Domotique ». Discu : pipeline « Discussion LLM », et chaque demande ouvre la fenêtre.' },
  { veux: 'Changer l’agent de conversation', ou: 'Home Assistant : Paramètres → Assistants vocaux → pipeline → Agent de conversation', comment: 'Choisir « Moteur IA (Domotique) », « Moteur IA (Discussion) », « Home Assistant » ou « Google AI ».', effet: 'Le texte reconnu part vers cet agent. Si l’intégration est recréée, les identifiants changent et les pipelines pointent dans le vide.' },
  { veux: 'Changer la voix de la réponse', ou: 'Home Assistant : pipeline → synthèse vocale', comment: 'Discussion : voix du cloud de Home Assistant. Domotique : moteur de phrases préenregistrées, voix laissée vide (une voix renseignée fait échouer l’appel).', effet: 'Une phrase hors catalogue change de voix en Domotique (repli sur une voix de secours).' },
  { veux: 'Changer le mot de réveil', ou: 'Firmware de l’écran + Home Assistant : select.tab5_mot_d_activation', comment: 'Un autre modèle exige une recompilation et un flash de l’écran.', effet: 'À ne faire qu’avec l’accord du responsable de l’écran. Un seul mot est proposé à Home Assistant.' },
  { veux: 'Couper ou remettre l’écoute', ou: 'Écran : bouton « Ok Nabu : ON / OFF » ; Home Assistant : switch.tab5_wake_word_active', comment: 'Allumer ou éteindre.', effet: 'Éteint, l’écran ne réagit plus qu’à l’appui sur le micro.' },
  { veux: 'Régler la fin de phrase', ou: 'Home Assistant : select.tab5_detection_de_fin_de_la_parole', comment: 'Options default, relaxed, aggressive : « relaxed » laisse plus de silence avant de couper.', effet: 'Joue sur les phrases coupées trop tôt ou trop tard.' },
  { veux: 'Régler le volume', ou: 'Écran : curseur de la fenêtre ; Home Assistant : number.tab5_volume (0 à 100)', comment: 'Régler à la valeur voulue.', effet: 'Constaté le 01/11 : le volume n’est pas conservé au redémarrage (retour à 100). Le remettre.' },
  { veux: 'Changer le modèle de langage du moteur', ou: 'Moteur : config.json → routing_policy.fast_path_providers (ordre de la discussion)', comment: 'Éditer la liste ; le moteur relit le fichier quand sa date change (d’après le code).', effet: 'Prochaine question. C’est une modification de la production : l’accord du responsable du moteur est nécessaire.' },
  { veux: 'Forcer un modèle ou un niveau pour un essai', ou: 'Corps de la requête : champs model et tier', comment: 'Le composant Home Assistant ne les envoie pas ; réservé aux essais à la main.', effet: 'Prioritaire sur la liste ci-dessus pour cette requête seulement.' },
  { veux: 'Changer ce que l’IA a le droit de piloter', ou: 'Moteur : catalogue ha_commands.json et tables de pièces du code', comment: 'Ajouter ou retirer des lignes, puis redéployer le moteur.', effet: 'Les agents natifs de Home Assistant obéissent à la liste « Exposer » ; le moteur ne l’utilise pas.' },
  { veux: 'Changer l’adresse ou la clé du moteur côté Home Assistant', ou: 'Entrée d’intégration « Moteur d’Agents » : host et api_key', comment: 'Scripts existants de synchronisation de la clé puis de rechargement de l’intégration.', effet: 'Prend effet au rechargement. Redémarrer Home Assistant demande l’accord du responsable.' },
  { veux: 'Changer les délais', ou: 'Composant Home Assistant (30 s et 35 s) et moteur (20 s en Discussion)', comment: 'Modifier puis recharger l’intégration.', effet: 'Le délai du moteur (20 s) tombe avant celui du composant (35 s).' },
];

/* ── Avant et après un tournage ──────────────────────────────────────────── */

export interface PointChecklist {
  id: string;
  titre: string;
  detail: string;
  /** Contrôle fait en direct par cette page, quand le moteur peut le voir. */
  controle?: 'moteur' | 'ha';
}

export const CHECKLIST_AVANT: PointChecklist[] = [
  { id: 'bouger', titre: 'Décider ce qui peut bouger', detail: 'Les pipelines du moteur commandent la vraie maison (lampes du salon, de la chambre, du chevet, volet de la serre, climatisation). Prévenir les personnes présentes. La « maison de démo » ne change que ce que l’écran affiche : la voix agit toujours sur la vraie maison.' },
  { id: 'moteur', titre: 'Moteur en marche', detail: 'Le moteur doit répondre « ok » sur sa page de santé, avec la version attendue.', controle: 'moteur' },
  { id: 'ha', titre: 'Home Assistant joignable', detail: 'Le moteur doit pouvoir joindre Home Assistant, sinon les ordres ne sont pas exécutés.', controle: 'ha' },
  { id: 'ecran', titre: 'Écran connecté à Home Assistant', detail: 'Dans Home Assistant, binary_sensor.tab5_ha_api_status doit valoir « on » (condition pour que « Okay Nabu » lance le pipeline). Non visible depuis le moteur.' },
  { id: 'ecoute', titre: 'Écoute activée', detail: 'switch.tab5_wake_word_active doit valoir « on ». Il valait « off » le 01/11 après un flash : à vérifier avant de filmer.' },
  { id: 'reglages', titre: 'Langue, volume, mode', detail: 'select.tab5_language = Français ; number.tab5_volume = 85 ; switch.tab5_speaker_enable = on ; bouton Domo ou Discu selon la prise.' },
  { id: 'essai', titre: 'Essai à blanc sans risque', detail: 'Dire « Quelle heure est-il ? » puis « Quel temps fait-il ? » : ces phrases ne commandent rien. Contrôler ensuite le passage dans le débogage du pipeline (Home Assistant) et dans « Rejouer une vraie demande » (page Comprendre le moteur).' },
  { id: 'calme', titre: 'Salle calme, plan B prêt', detail: 'La reconnaissance vocale est le maillon faible. Plan B : taper la phrase dans la barre de conversation de Home Assistant.' },
];

export interface PointRetour {
  quoi: string;
  etat: string;
  comment: string;
}

export const RETOUR_NORMAL: PointRetour[] = [
  { quoi: 'Langue de l’écran', etat: 'Déjà normale (Français) à la lecture', comment: 'Si besoin : choisir « Français » (l’écran redémarre).' },
  { quoi: 'Sélecteurs de pipeline', etat: 'Déjà normaux à la lecture', comment: 'Si besoin : choisir « Discussion LLM » dans « Tab5 · chat pipeline » ; ne pas toucher à select.tab5_assistant.' },
  { quoi: 'Automatisation temporaire du rideau en anglais', etat: 'Encore allumée à la lecture', comment: 'Paramètres → Automatisations et scènes → l’ouvrir → ⋮ → Supprimer (sa description dit « à supprimer après le tournage »).' },
  { quoi: 'Alias anglais (« curtain », « living room shutter », « living room lamp »)', etat: 'Toujours présents à la lecture', comment: 'Paramètres → Appareils et services → Entités → l’entité → roue dentée → Assistants vocaux → retirer uniquement ces alias.' },
  { quoi: 'Pipeline temporaire « English (video) »', etat: 'Toujours présent à la lecture', comment: 'Le supprimer après avoir vérifié qu’aucun sélecteur ne le porte et noté ses réglages si une autre prise anglaise est prévue.' },
  { quoi: 'Volume', etat: '85 à la lecture', comment: 'Remettre 85 si l’écran a redémarré (retour à 100).' },
  { quoi: 'Écoute du mot de réveil', etat: 'Éteinte à la lecture', comment: 'Remettre switch.tab5_wake_word_active sur « on ».' },
  { quoi: 'Maison de démo', etat: 'Non utilisée dans cette relève', comment: 'Si elle l’a été : lancer script.demo_tournage_fin.' },
];

/* ── Dépannage et limites ────────────────────────────────────────────────── */

export interface Depannage {
  symptome: string;
  cause: string;
  regarder: string;
}

export const DEPANNAGE: Depannage[] = [
  { symptome: 'Rien ne se passe après « Okay Nabu »', cause: 'Écoute coupée, écran déconnecté de Home Assistant, ou mot mal entendu.', regarder: 'Popup « Ok Nabu : OFF » ; binary_sensor.tab5_ha_api_status ; journal de l’écran.' },
  { symptome: 'Le micro passe au rouge, « Erreur »', cause: 'Aucun texte reconnu : constaté sur 5 passages sur 10 en Discussion le 01/11, et 1 sur 10 en Domotique.', regarder: 'Débogage du pipeline ; rapprocher le micro ; réglage de fin de phrase ; accès Internet de Home Assistant.' },
  { symptome: '« Je n’ai pas compris la commande domotique. »', cause: 'Mode Domotique : phrase absente du catalogue et sans verbe d’action reconnu.', regarder: 'Page Comprendre le moteur → rejouer l’échange (type « ha_mode_failure ») ; essayer en Discu ; ajouter la phrase au catalogue.' },
  { symptome: '« Impossible de contacter le moteur d’agents pour le moment. »', cause: 'Moteur éteint, injoignable, ou délai de 30 s / 35 s dépassé.', regarder: 'Page de santé du moteur (étape « Moteur en marche » ci-dessus) ; journal du service sur le Deck ; journal de Home Assistant.' },
  { symptome: '« Erreur du serveur de moteur IA (Code: 403) »', cause: 'La clé enregistrée dans Home Assistant diffère de celle du moteur.', regarder: 'Script de synchronisation de la clé, puis rechargement de l’intégration.' },
  { symptome: '« Désolé, je n’ai pas su répondre. Peux-tu reformuler ? »', cause: 'Discussion : tous les modèles ont échoué ou 20 s dépassées.', regarder: 'Rejouer l’échange (type « discussion_chat_failure ») ; cascade et quotas des fournisseurs gratuits.' },
  { symptome: 'La réponse s’affiche mais aucun son, ou un son coupé', cause: 'Volume à zéro, haut-parleur coupé, ou synthèse vocale en erreur.', regarder: 'number.tab5_volume ; switch.tab5_speaker_enable ; débogage du pipeline.' },
  { symptome: 'La voix change entre deux réponses (Domotique)', cause: 'Phrase hors du catalogue préenregistré : repli sur une voix de secours.', regarder: 'Catalogue de phrases du moteur (dossier tts_local_cache).' },
  { symptome: 'La fenêtre ne s’ouvre pas', cause: 'Normal en Domo : elle ne s’ouvre qu’en Discu.', regarder: 'Appui long sur le micro pour l’ouvrir à la main.' },
  { symptome: 'Une question a allumé ou éteint quelque chose', cause: 'Un texte mal reconnu ressemble à un ordre.', regarder: 'Rejouer l’échange pour lire le texte exact et le chemin pris ; garde-fou du code.' },
  { symptome: 'Le volet a bougé pendant un essai', cause: 'Normal : le moteur commande la vraie maison.', regarder: 'Voir l’avertissement en haut de page.' },
];

export const LIMITES: string[] = [
  '**Vraie maison.** Les pipelines « Domotique » et « Discussion LLM » commandent les appareils réels. Le volet du salon a bougé pendant un « essai » le 01/11. Toujours prévenir avant une action réelle.',
  '**La reconnaissance vocale est le maillon faible.** Mesures du 19/10 sur audio propre : l’ancien [[stt|STT]] du Deck faisait 12,1 % d’erreurs de mots, le cloud de Home Assistant 3,8 %. Le moteur rattrape certaines erreurs, c’est utile mais fragile.',
  '**Latence.** De 3 à 9 s entre la fin de la phrase et la fin du passage dans Home Assistant ; la discussion libre ajoute environ 3 s de modèle. La part de la synthèse vocale n’est pas mesurée.',
  '**Coûts.** Le coût réel d’une question vocale n’a pas été mesuré. La discussion commence par un modèle gratuit, puis un modèle bon marché.',
  '**Dépendances.** Internet (reconnaissance vocale, voix, modèles), le Steam Deck (un seul moteur, en production) et la machine virtuelle Home Assistant. Si le Deck est éteint, seule la phrase « Impossible de contacter le moteur » est dite.',
  '**Confidentialité.** L’audio de la phrase part vers le cloud de Home Assistant pour être transcrit ; le texte part vers les fournisseurs de modèles pour la discussion.',
  '**Pas de flux continu.** Le composant Home Assistant en service appelle la route synchrone du moteur : l’idée que le trafic vocal est « streamé » est inexacte pour ce trajet.',
  '**Prise anglaise.** Le pipeline « English (video) » n’utilise pas le moteur. Si une vidéo est présentée comme « via le moteur », elle ne doit montrer que les prises françaises.',
];

/** Phrases proposées dans la zone « Essayer une phrase » (la simulation n'exécute rien). */
export const PHRASES_ESSAI: Array<{ texte: string; mode: 'ha' | 'chat'; note?: string }> = [
  { texte: 'Quelle heure est-il ?', mode: 'ha' },
  { texte: 'Quel temps fait-il ?', mode: 'ha' },
  { texte: 'Allume la lumière du salon', mode: 'ha', note: 'ordre, simulé seulement' },
  { texte: 'Raconte-moi une blague', mode: 'chat' },
];
