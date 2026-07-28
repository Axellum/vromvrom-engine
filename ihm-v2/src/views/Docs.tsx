/**
 * Vue « Documentation ».
 *
 * Nouvelle section. Objectif : rendre le moteur compréhensible sans lire son
 * code. Deux registres distincts :
 *  - « Comprendre » : ce que fait le moteur, mécanisme par mécanisme.
 *  - « Optimiser »  : des recettes concrètes, avec le réglage exact à toucher.
 *
 * Règle d'écriture : ne décrire que des comportements vérifiés dans le code du
 * moteur, et nommer le fichier ou le réglage responsable. Quand une limite est
 * connue, elle est dite plutôt que passée sous silence.
 */
import { useState } from 'react';
import { BookOpen, Zap, Coins, Mic, ShieldCheck, ArrowRight } from 'lucide-react';
import { useUiStore, type ViewKey } from '../state/uiStore';
import { PageHeader, Card, Explain } from '../components/ui/primitives';

/* ── Éléments de rédaction ────────────────────────────────────────────────── */

function P({ children }: { children: React.ReactNode }) {
  return <p className="text-sm leading-relaxed text-slate-400">{children}</p>;
}

function H({ children }: { children: React.ReactNode }) {
  return <h3 className="mt-5 text-sm font-semibold text-slate-200 first:mt-0">{children}</h3>;
}

function K({ children }: { children: React.ReactNode }) {
  return (
    <code className="rounded bg-slate-800 px-1 py-0.5 font-mono text-[11px] text-sky-300">
      {children}
    </code>
  );
}

function Steps({ items }: { items: React.ReactNode[] }) {
  return (
    <ol className="space-y-2">
      {items.map((item, i) => (
        <li key={i} className="flex gap-3 text-sm text-slate-400">
          <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-slate-800 text-[11px] font-medium text-sky-300">
            {i + 1}
          </span>
          <span className="leading-relaxed">{item}</span>
        </li>
      ))}
    </ol>
  );
}

/** Lien interne vers un autre onglet — la doc renvoie vers l'écran qui agit. */
function GoTo({ view, children }: { view: ViewKey; children: React.ReactNode }) {
  const setView = useUiStore((s) => s.setView);
  return (
    <button
      onClick={() => setView(view)}
      className="inline-flex items-center gap-1 font-medium text-sky-400 transition hover:text-sky-300"
    >
      {children}
      <ArrowRight className="h-3 w-3" />
    </button>
  );
}

/* ── Contenu ──────────────────────────────────────────────────────────────── */

interface DocSection {
  id: string;
  title: string;
  subtitle: string;
  body: React.ReactNode;
}

const UNDERSTAND: DocSection[] = [
  {
    id: 'flow',
    title: "Le trajet d'une demande",
    subtitle: "De votre phrase jusqu'à la réponse, étape par étape.",
    body: (
      <div className="space-y-3">
        <Steps
          items={[
            <>
              <strong className="text-slate-300">Entrée.</strong> La demande arrive par le Chat,
              par <K>/api/run</K>, ou depuis le Tab5 en vocal. Le champ <K>source</K> indique
              d'où elle vient — le moteur ne traite pas une commande vocale comme une requête
              d'IDE.
            </>,
            <>
              <strong className="text-slate-300">Routage.</strong> Le routeur classe la demande.
              Un <em>fast-path</em> par expressions régulières attrape les cas évidents (allumer
              une lumière) sans appeler de LLM ; sinon un classifieur LLM tranche. La décision
              est enregistrée dans <K>routing_decisions</K>.
            </>,
            <>
              <strong className="text-slate-300">Planification.</strong> Pour une tâche
              complexe, le Planner découpe en graphe de tâches (DAG) avec leurs dépendances.
              Les tâches indépendantes peuvent s'exécuter en parallèle.
            </>,
            <>
              <strong className="text-slate-300">Exécution.</strong> Chaque tâche part vers un
              agent, qui demande un modèle à la passerelle selon son tier. C'est là qu'intervient
              la cascade.
            </>,
            <>
              <strong className="text-slate-300">Revue.</strong> Si la revue automatique est
              active, le Reviewer note le résultat. Sous le seuil de qualité, la tâche peut être
              rejouée sur un tier supérieur.
            </>,
            <>
              <strong className="text-slate-300">Restitution.</strong> La réponse revient, et la
              consommation est comptabilisée dans <K>token_usage</K>. En vocal, le Tab5 la
              vocalise.
            </>,
          ]}
        />
        <P>
          Chaque étape émet un événement sur le flux temps réel <K>/api/stream</K> — c'est ce
          que vous voyez défiler dans <GoTo view="dashboard">la vue d'ensemble</GoTo>.
        </P>
      </div>
    ),
  },
  {
    id: 'cascade',
    title: 'La cascade de modèles',
    subtitle: "Comment le moteur choisit qui répond, et pourquoi ça coûte peu.",
    body: (
      <div className="space-y-3">
        <P>
          Le moteur ne s'adresse jamais à « un modèle » : il s'adresse à un <strong>tier</strong>{' '}
          — <K>leger</K>, <K>moyen</K>, <K>fort</K> ou <K>automatique</K>. Chaque tier est une
          liste ordonnée de modèles.
        </P>
        <H>Le principe</H>
        <P>
          Le moteur essaie le premier modèle de la liste. S'il est indisponible (quota épuisé,
          erreur, circuit ouvert), il passe au suivant. La logique d'ensemble va du moins cher
          au plus cher : local, puis gratuit, puis forfait déjà payé, puis API facturée à
          l'usage.
        </P>
        <P>
          Conséquence pratique : <strong>l'ordre de la liste est votre principal levier
          d'économie</strong>. Un modèle gratuit en tête d'un tier très sollicité absorbe
          l'essentiel du trafic. Cet ordre s'édite dans{' '}
          <GoTo view="config">Configuration → Composition des tiers</GoTo>.
        </P>
        <H>Ce qui peut couper la cascade</H>
        <P>
          Un modèle listé dans <K>routing_policy.excluded_models</K> ne sera jamais choisi
          automatiquement, même s'il figure dans un tier. Ce garde-fou existe pour éviter
          qu'une escalade n'atteigne silencieusement le modèle le plus cher du catalogue. Il
          reste appelable explicitement.
        </P>
      </div>
    ),
  },
  {
    id: 'breakers',
    title: 'Circuit breakers',
    subtitle: "Pourquoi une panne de provider ne bloque pas le moteur.",
    body: (
      <div className="space-y-3">
        <P>
          Chaque provider est protégé par un disjoncteur. Après plusieurs échecs consécutifs, le
          circuit s'<strong>ouvre</strong> : le moteur cesse d'appeler ce provider et passe
          immédiatement au suivant de la cascade, sans attendre le délai d'expiration.
        </P>
        <P>
          Après une période de repos, le circuit passe en <strong>half-open</strong> : un appel
          test décide s'il se referme ou se rouvre. Un circuit <strong>fermé</strong> est l'état
          normal.
        </P>
        <P>
          Ces compteurs vivent en mémoire du serveur : ils repartent de zéro à chaque
          redémarrage, et un moteur fraîchement démarré affiche donc zéro circuit — ce n'est pas
          une anomalie. État courant dans <GoTo view="operations">Opérations</GoTo>.
        </P>
      </div>
    ),
  },
  {
    id: 'budget',
    title: 'BudgetGuard',
    subtitle: "Le garde-fou qui vous évite une facture surprise.",
    body: (
      <div className="space-y-3">
        <P>
          Avant chaque décision de cascade, le moteur vérifie ses plafonds. Une fois le budget
          journalier atteint, il refuse de router vers un provider payant et se rabat sur le
          gratuit. La tâche n'échoue pas : elle est traitée moins finement.
        </P>
        <P>
          Le garde-fou surveille aussi les quotas gratuits (tokens/heure côté Gemini,
          requêtes/jour côté DeepSeek) pour anticiper le rejet plutôt que de le subir, et
          consulte le solde réel DeepSeek via son API officielle avant de router vers son
          palier gratuit.
        </P>
        <P>
          Plafonds réglables dans{' '}
          <GoTo view="config">Configuration → Budgets & garde-fous</GoTo>.
        </P>
      </div>
    ),
  },
  {
    id: 'cache',
    title: 'Cache sémantique',
    subtitle: "Répondre sans appeler de modèle.",
    body: (
      <div className="space-y-3">
        <P>
          Le cache compare la demande entrante aux précédentes par similarité d'embeddings.
          Au-dessus du seuil, il renvoie la réponse déjà produite : coût nul, réponse immédiate.
        </P>
        <P>
          Le réglage sensible est le <strong>seuil de similarité</strong>. Trop bas, le moteur
          ressort une réponse voisine mais hors sujet — pour une commande domotique, il peut
          agir sur la mauvaise pièce. <K>0.95</K> est un compromis prudent.
        </P>
        <P>Désactivé par défaut.</P>
      </div>
    ),
  },
  {
    id: 'agents',
    title: 'Les agents',
    subtitle: "Qui fait quoi dans le moteur.",
    body: (
      <div className="space-y-3">
        <H>Agents de traitement</H>
        <P>
          Le <strong>Planner</strong> découpe une demande en DAG. L'<strong>Executor</strong>{' '}
          exécute chaque étape — c'est le plus sollicité, donc celui qui pèse le plus sur la
          facture. Le <strong>Reviewer</strong> note le résultat. L'<strong>agent HA</strong>{' '}
          traduit une intention en appel de service Home Assistant.
        </P>
        <H>Agents autonomes</H>
        <P>
          Le <strong>Daemon Sentinelle</strong> surveille le système à intervalle fixe et
          alerte sans corriger. Le <strong>Dreamer</strong> consolide la mémoire la nuit ; sa
          variante <strong>DreamCoder</strong> puise dans le backlog et écrit réellement du code
          sur une branche Git. L'<strong>Auditeur</strong> relit le code par lots — désactivé
          par défaut car coûteux en tokens d'entrée.
        </P>
        <P>
          Détail et déclenchement dans <GoTo view="autonomy">Agents autonomes</GoTo>.
        </P>
      </div>
    ),
  },
  {
    id: 'vocal',
    title: "L'assistant vocal",
    subtitle: "Du micro du Tab5 à la réponse parlée.",
    body: (
      <div className="space-y-3">
        <P>
          Le Tab5 transcrit la parole (STT) et envoie le texte à <K>/api/execute</K> avec une
          source <K>tab5</K>. Le routeur distingue une commande domotique (vers l'agent HA)
          d'une question ouverte (mode Discussion). La réponse revient et le Tab5 la vocalise.
        </P>
        <H>Ce qui est journalisé</H>
        <P>
          Chaque échange écrit deux lignes dans <K>vocal_audit_log</K> : la requête puis la
          réponse, avec la transcription exacte, le routage retenu et la latence mesurée. Quand
          une commande vocale « ne marche pas », ce journal montre ce que le STT a réellement
          compris — c'est presque toujours là que se trouve la cause. Consultable dans{' '}
          <GoTo view="homeassistant">Domotique & Vocal</GoTo>.
        </P>
        <H>Barge-in</H>
        <P>
          Le moteur peut interrompre une réponse vocale en cours (<K>/api/vocal/abort</K>), pour
          que vous puissiez reprendre la parole sans attendre la fin.
        </P>
      </div>
    ),
  },
  {
    id: 'elo',
    title: 'Scoring Elo',
    subtitle: "Et sa limite actuelle, qu'il vaut mieux connaître.",
    body: (
      <div className="space-y-3">
        <P>
          Le moteur tient un score de type Elo par domaine, alimenté par les succès et échecs
          observés, pour ordonner les candidats à qualité comparable.
        </P>
        <P>
          <strong>Limite à connaître :</strong> en pratique, la table de scores est indexée sur
          les <em>agents</em> (<K>planner</K>, <K>executor</K>…) et non sur les modèles. Les
          colonnes Elo du comparatif restent donc souvent vides pour les modèles. C'est une
          limite réelle, pas un défaut d'affichage — et c'est pourquoi la{' '}
          <GoTo view="benchmarks">performance observée</GoTo> (appels, coût, latence) est un
          guide plus fiable pour arbitrer.
        </P>
      </div>
    ),
  },
];

const OPTIMIZE: DocSection[] = [
  {
    id: 'cost',
    title: 'Réduire les coûts',
    subtitle: "Par ordre d'impact décroissant.",
    body: (
      <div className="space-y-3">
        <Steps
          items={[
            <>
              <strong className="text-slate-300">Identifier le vrai poste de dépense.</strong>{' '}
              Dans <GoTo view="benchmarks">Comparatif modèles</GoTo>, triez la performance
              observée par coût total. Un ou deux modèles concentrent presque toujours la
              facture.
            </>,
            <>
              <strong className="text-slate-300">Reléguer le coupable.</strong> S'il est en tête
              d'un tier très sollicité, descendez-le dans la liste et placez une alternative
              gratuite au-dessus (<GoTo view="config">Composition des tiers</GoTo>). Le moteur
              n'atteindra le modèle payant que si le gratuit est indisponible.
            </>,
            <>
              <strong className="text-slate-300">Baisser le tier de l'Executor.</strong> C'est
              l'agent le plus appelé. Passer de <K>fort</K> à <K>moyen</K> a plus d'effet que
              n'importe quel autre changement de modèle.
            </>,
            <>
              <strong className="text-slate-300">Espacer le Daemon.</strong> Un cycle toutes les
              10 minutes fait 144 appels par jour, même quand vous ne demandez rien. Doubler
              l'intervalle divise ce coût de fond par deux.
            </>,
            <>
              <strong className="text-slate-300">Restreindre l'escalade qualité.</strong>{' '}
              Chaque escalade double le coût de la tâche. Baissez le seuil de qualité ou
              limitez les domaines éligibles.
            </>,
            <>
              <strong className="text-slate-300">Poser un plafond.</strong> Le budget
              journalier de BudgetGuard est le filet de sécurité : au-delà, plus aucun appel
              payant.
            </>,
          ]}
        />
      </div>
    ),
  },
  {
    id: 'speed',
    title: 'Accélérer les réponses',
    subtitle: "Là où se cachent les secondes.",
    body: (
      <div className="space-y-3">
        <Steps
          items={[
            <>
              <strong className="text-slate-300">Mesurer avant de régler.</strong> Le comparatif
              manuel donne la latence réelle de chaque modèle sur <em>votre</em> réseau. Les
              écarts entre modèles se comptent souvent en secondes, pas en millisecondes.
            </>,
            <>
              <strong className="text-slate-300">Mettre le plus rapide en tête</strong> du tier{' '}
              <K>leger</K>. C'est ce tier qui sert les réponses courtes et le vocal.
            </>,
            <>
              <strong className="text-slate-300">Activer le cache sémantique.</strong> Sur des
              demandes répétitives, la réponse devient instantanée.
            </>,
            <>
              <strong className="text-slate-300">Désactiver la revue automatique</strong> si la
              vitesse prime : elle ajoute un appel LLM à chaque exécution.
            </>,
            <>
              <strong className="text-slate-300">Vérifier les circuits.</strong> Un circuit qui
              s'ouvre et se referme en boucle ajoute des tentatives perdues avant chaque
              réponse. Visible dans <GoTo view="operations">Opérations</GoTo>.
            </>,
          ]}
        />
      </div>
    ),
  },
  {
    id: 'vocal-reliability',
    title: 'Fiabiliser le vocal',
    subtitle: "Quand « ça ne comprend pas ».",
    body: (
      <div className="space-y-3">
        <Steps
          items={[
            <>
              <strong className="text-slate-300">Lire le journal d'abord.</strong> Dans{' '}
              <GoTo view="homeassistant">Domotique & Vocal</GoTo>, le journal montre la
              transcription exacte. Neuf fois sur dix, le moteur a correctement traité ce qu'il
              a reçu — c'est le STT qui a mal entendu.
            </>,
            <>
              <strong className="text-slate-300">Vérifier que l'entité existe.</strong> Une
              commande qui ne fait rien vise souvent une entité renommée ou supprimée.
              L'explorateur d'entités donne la liste exacte sur laquelle le moteur peut agir.
            </>,
            <>
              <strong className="text-slate-300">Viser un tier léger.</strong> Au-delà de deux
              secondes, un échange vocal paraît cassé. Mieux vaut une réponse simple et rapide
              qu'une réponse fine et lente.
            </>,
            <>
              <strong className="text-slate-300">Surveiller la latence moyenne</strong> dans les
              statistiques vocales. Une dérive signale généralement un modèle de tête devenu
              lent ou indisponible.
            </>,
            <>
              <strong className="text-slate-300">Contrôler la liaison HA.</strong> Si le
              diagnostic d'<GoTo view="setup">Installation</GoTo> signale Home Assistant
              injoignable, aucune commande domotique ne peut aboutir, quelle que soit la
              qualité de la transcription.
            </>,
          ]}
        />
      </div>
    ),
  },
  {
    id: 'quality',
    title: 'Améliorer la qualité',
    subtitle: "Quand la réponse est juste mais médiocre.",
    body: (
      <div className="space-y-3">
        <Steps
          items={[
            <>
              <strong className="text-slate-300">Soigner le Planner avant l'Executor.</strong>{' '}
              Un mauvais découpage coûte plus cher à rattraper qu'il n'aurait coûté à bien
              faire. C'est le seul agent où monter en tier se rentabilise franchement.
            </>,
            <>
              <strong className="text-slate-300">Activer la revue automatique</strong> et
              l'escalade qualité, en gardant l'escalade limitée aux domaines qui le méritent.
            </>,
            <>
              <strong className="text-slate-300">Comparer sur vos vrais prompts.</strong> Un
              modèle excellent en général peut être médiocre sur votre domotique. Le comparatif
              manuel sert exactement à ça.
            </>,
            <>
              <strong className="text-slate-300">Travailler le prompt.</strong>{' '}
              <GoTo view="prompt">Prompt Studio</GoTo> permet de reformuler une consigne avant
              de la confier au moteur — souvent plus efficace que de changer de modèle.
            </>,
          ]}
        />
      </div>
    ),
  },
  {
    id: 'safety',
    title: 'Garder la main',
    subtitle: "Ce qui agit tout seul, et comment l'encadrer.",
    body: (
      <div className="space-y-3">
        <P>
          Plusieurs mécanismes agissent sans validation : le DreamCoder écrit du code et crée
          des branches Git, l'agent HA pilote réellement votre domicile, et le Daemon tourne en
          continu.
        </P>
        <Steps
          items={[
            <>
              Laisser <K>hitl.interactive_auto_approve</K> désactivé : c'est ce réglage qui
              conserve un point de contrôle humain avant les actions jugées risquées.
            </>,
            <>
              Vérifier <K>dreamcoder_repo_path</K> avant d'activer le DreamCoder — il écrit
              vraiment dans ce dépôt.
            </>,
            <>
              Poser un plafond de coût par session <em>et</em> un budget journalier : le premier
              borne une dérive isolée, le second une dérive lente.
            </>,
            <>
              Passer en revue le backlog régulièrement : approuver une tâche y déclenche une
              vraie fusion Git.
            </>,
          ]}
        />
      </div>
    ),
  },
];

/* ── Page ─────────────────────────────────────────────────────────────────── */

type Tab = 'understand' | 'optimize';

export function Docs() {
  const [tab, setTab] = useState<Tab>('understand');
  const [active, setActive] = useState<string>('flow');

  const sections = tab === 'understand' ? UNDERSTAND : OPTIMIZE;
  const current = sections.find((s) => s.id === active) ?? sections[0];

  function switchTab(next: Tab) {
    setTab(next);
    setActive(next === 'understand' ? UNDERSTAND[0].id : OPTIMIZE[0].id);
  }

  return (
    <div className="mx-auto max-w-6xl pb-20">
      <PageHeader
        title="Documentation"
        description="Comprendre ce que fait le moteur, puis le régler pour votre usage."
      />

      <div className="mb-6 flex gap-2">
        <button
          onClick={() => switchTab('understand')}
          className={`inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition ${
            tab === 'understand'
              ? 'bg-sky-600 text-white'
              : 'border border-slate-700 text-slate-400 hover:bg-slate-800 hover:text-slate-200'
          }`}
        >
          <BookOpen className="h-4 w-4" />
          Comprendre
        </button>
        <button
          onClick={() => switchTab('optimize')}
          className={`inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition ${
            tab === 'optimize'
              ? 'bg-sky-600 text-white'
              : 'border border-slate-700 text-slate-400 hover:bg-slate-800 hover:text-slate-200'
          }`}
        >
          <Zap className="h-4 w-4" />
          Optimiser
        </button>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[220px_1fr]">
        {/* Sommaire */}
        <nav className="space-y-1 self-start lg:sticky lg:top-6">
          {sections.map((s) => (
            <button
              key={s.id}
              onClick={() => setActive(s.id)}
              className={`w-full rounded-lg px-3 py-2 text-left text-sm transition ${
                current.id === s.id
                  ? 'bg-slate-800 font-medium text-sky-300'
                  : 'text-slate-400 hover:bg-slate-800/50 hover:text-slate-200'
              }`}
            >
              {s.title}
            </button>
          ))}
        </nav>

        {/* Contenu */}
        <div className="min-w-0 space-y-6">
          <Card title={current.title} subtitle={current.subtitle}>
            {current.body}
          </Card>

          {tab === 'optimize' && (
            <Explain title="Une seule chose à la fois">
              <p>
                Changez un réglage, laissez tourner, puis revenez mesurer dans{' '}
                <GoTo view="observability">Observabilité</GoTo>. Modifier trois paramètres
                d'un coup rend impossible d'attribuer une amélioration — ou une régression.
              </p>
            </Explain>
          )}

          {tab === 'understand' && current.id === 'flow' && (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <MiniCard icon={Coins} title="Ce qui coûte" text="L'Executor et le Daemon : le premier par volume, le second par répétition." />
              <MiniCard icon={Mic} title="Ce qui doit être rapide" text="Le tier leger, qui sert le vocal et les réponses courtes." />
              <MiniCard icon={ShieldCheck} title="Ce qui vous protège" text="BudgetGuard, les circuits, et le point de contrôle humain." />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function MiniCard({ icon: Icon, title, text }: {
  icon: React.ElementType;
  title: string;
  text: string;
}) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
      <Icon className="h-4 w-4 text-sky-400" />
      <p className="mt-2 text-sm font-medium text-slate-200">{title}</p>
      <p className="mt-1 text-xs leading-relaxed text-slate-500">{text}</p>
    </div>
  );
}

export default Docs;
