/**
 * Vue « Comparatif modèles ».
 *
 * Remplace l'ancienne page Benchmarks, dont les trois tableaux étaient des
 * données inventées : les endpoints appelés renvoyaient 404 et le module API
 * repliait silencieusement sur des mocks. Ici, tout provient de mesures réelles,
 * et ce qui n'a jamais été mesuré s'affiche « — ».
 */
import { useMemo, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Play, Trophy, Timer, Coins } from 'lucide-react';
import {
  fetchBenchmarks,
  runBenchmark,
  fetchElo,
  type ObservedModelPerf,
  type BenchmarkRun,
} from '../api/benchmarks';
import { fetchModels } from '../api/models';
import {
  PageHeader, Card, Explain, Warn, Table, Th, Td, Value, Button,
  LoadingState, ErrorState, EmptyState, PausedState, queryPhase, Metric, Pill,
} from '../components/ui/primitives';

const MAX_MODELS = 8; // aligné sur _BENCH_MAX_MODELS côté backend

type ObsSortKey = 'calls' | 'cost_usd' | 'avg_cost_per_call_usd' | 'avg_latency_ms' | 'elo';

/* ── Lancer un comparatif ─────────────────────────────────────────────────── */

function RunBenchmarkCard() {
  const queryClient = useQueryClient();
  const [prompt, setPrompt] = useState('Explique en trois phrases ce qu\'est un circuit breaker.');
  const [selected, setSelected] = useState<string[]>([]);
  const [maxTokens, setMaxTokens] = useState(512);

  const modelsQ = useQuery({ queryKey: ['models'], queryFn: fetchModels });

  // Seuls les modèles réellement câblés dans le gateway peuvent être appelés.
  const candidates = useMemo(
    () => (modelsQ.data ?? []).filter((m) => m.is_wired && m.enabled),
    [modelsQ.data],
  );

  const run = useMutation({
    mutationFn: () => runBenchmark(prompt, selected, { maxTokens }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['benchmarks'] }),
  });

  function toggle(id: string) {
    setSelected((cur) =>
      cur.includes(id) ? cur.filter((m) => m !== id) : cur.length < MAX_MODELS ? [...cur, id] : cur,
    );
  }

  const freeSelected = selected.filter((id) =>
    candidates.find((c) => c.id === id && (c.cost_output_per_m ?? 0) === 0),
  ).length;
  const paidSelected = selected.length - freeSelected;

  return (
    <Card
      title="Lancer un comparatif"
      subtitle="Le même prompt part vers chaque modèle en parallèle ; la latence est chronométrée côté serveur."
      source="POST /api/metrics/benchmarks"
    >
      <div className="space-y-4">
        <Warn>
          Ce bouton déclenche de <strong>vrais appels LLM</strong>, facturés selon le modèle.
          La consommation est comptabilisée dans le suivi de tokens du moteur, comme n'importe
          quel appel. Maximum {MAX_MODELS} modèles par comparatif.
        </Warn>

        <div>
          <label className="mb-1.5 block text-xs font-medium text-slate-400">Prompt de test</label>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            rows={3}
            className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200 outline-none focus:border-sky-500"
            placeholder="Prompt envoyé à tous les modèles sélectionnés…"
          />
        </div>

        <div>
          <div className="mb-1.5 flex items-center justify-between">
            <label className="text-xs font-medium text-slate-400">
              Modèles à comparer ({selected.length}/{MAX_MODELS})
            </label>
            {paidSelected > 0 && (
              <span className="text-xs text-amber-400">
                {paidSelected} payant{paidSelected > 1 ? 's' : ''} sélectionné{paidSelected > 1 ? 's' : ''}
              </span>
            )}
          </div>
          {queryPhase(modelsQ) === 'loading' ? (
            <LoadingState label="Chargement du catalogue…" />
          ) : queryPhase(modelsQ) === 'paused' ? (
            <PausedState onRetry={() => modelsQ.refetch()} />
          ) : queryPhase(modelsQ) === 'error' ? (
            <ErrorState error={modelsQ.error} onRetry={() => modelsQ.refetch()} />
          ) : candidates.length === 0 ? (
            <EmptyState>Aucun modèle actif et câblé dans le gateway.</EmptyState>
          ) : (
            <div className="max-h-56 overflow-y-auto rounded-lg border border-slate-800 p-2">
              <div className="flex flex-wrap gap-1.5">
                {candidates.map((m) => {
                  const on = selected.includes(m.id);
                  const isFree = (m.cost_output_per_m ?? 0) === 0;
                  return (
                    <button
                      key={m.id}
                      onClick={() => toggle(m.id)}
                      disabled={!on && selected.length >= MAX_MODELS}
                      title={isFree ? 'Gratuit / forfait' : `~$${m.cost_output_per_m}/M tokens sortie`}
                      className={`rounded-md px-2 py-1 font-mono text-xs transition disabled:opacity-40 ${
                        on
                          ? 'bg-sky-600 text-white'
                          : 'border border-slate-700 text-slate-400 hover:border-slate-600 hover:text-slate-200'
                      }`}
                    >
                      {m.id}
                      {!isFree && <span className="ml-1 text-amber-400">$</span>}
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>

        <div className="flex flex-wrap items-end gap-4">
          <div>
            <label className="mb-1.5 block text-xs font-medium text-slate-400">Tokens max / réponse</label>
            <input
              type="number"
              min={1}
              max={2048}
              value={maxTokens}
              onChange={(e) => setMaxTokens(Number(e.target.value))}
              className="w-28 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm outline-none focus:border-sky-500"
            />
          </div>
          <Button
            onClick={() => run.mutate()}
            disabled={run.isPending || selected.length === 0 || !prompt.trim()}
          >
            <Play className="h-4 w-4" />
            {run.isPending ? 'Comparatif en cours…' : `Lancer sur ${selected.length} modèle${selected.length > 1 ? 's' : ''}`}
          </Button>
        </div>

        {run.isError && <ErrorState error={run.error} />}
      </div>
    </Card>
  );
}

/* ── Résultats d'un comparatif ────────────────────────────────────────────── */

function RunResults({ run }: { run: BenchmarkRun }) {
  const ok = run.results.filter((r) => r.status === 'success' && r.latency_ms !== null);
  const fastest = ok.length ? ok.reduce((a, b) => ((a.latency_ms ?? 0) <= (b.latency_ms ?? 0) ? a : b)) : null;
  const totalCost = run.results.reduce((s, r) => s + (r.cost_usd ?? 0), 0);

  return (
    <Card
      title="Dernier comparatif"
      subtitle={
        <>
          « {run.prompt.slice(0, 90)}{run.prompt.length > 90 ? '…' : ''} » —{' '}
          {new Date(run.created_at * 1000).toLocaleString('fr-FR')}
        </>
      }
      source="GET /api/metrics/benchmarks"
    >
      <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Metric label="Modèles testés" value={run.results.length} />
        <Metric label="Réussis" value={ok.length} tone={ok.length === run.results.length ? 'good' : 'warn'} />
        <Metric
          label="Plus rapide"
          value={fastest ? `${Math.round(fastest.latency_ms ?? 0)} ms` : '—'}
          hint={fastest?.model}
          tone="good"
        />
        <Metric label="Coût du run" value={`$${totalCost.toFixed(6)}`} />
      </div>

      <Table>
        <thead className="bg-slate-900/80">
          <tr>
            <Th>Modèle</Th>
            <Th align="right">Latence mesurée</Th>
            <Th align="right">Tokens (in/out)</Th>
            <Th align="right">Coût</Th>
            <Th>Réponse</Th>
          </tr>
        </thead>
        <tbody>
          {run.results.map((r) => (
            <tr key={r.model} className="border-t border-slate-800/60 align-top">
              <Td className="font-mono text-sky-300">
                <div className="flex items-center gap-1.5">
                  {r.model === fastest?.model && <Trophy className="h-3.5 w-3.5 text-emerald-400" />}
                  {r.model}
                </div>
                {r.status === 'error' && <Pill tone="bad">échec</Pill>}
              </Td>
              <Td align="right" className="font-mono text-slate-300">
                <Value value={r.latency_ms} unit=" ms" digits={0} />
              </Td>
              <Td align="right" className="font-mono text-slate-400">
                <Value value={r.prompt_tokens} /> / <Value value={r.completion_tokens} />
              </Td>
              <Td align="right" className="font-mono text-amber-300">
                {r.cost_usd === null ? <Value value={null} /> : `$${r.cost_usd.toFixed(6)}`}
              </Td>
              <Td className="max-w-md text-xs text-slate-400">
                {r.status === 'error' ? (
                  <span className="text-red-400">{r.error_message}</span>
                ) : (
                  <span className="line-clamp-3">{r.response_text}</span>
                )}
              </Td>
            </tr>
          ))}
        </tbody>
      </Table>

      <p className="mt-3 text-xs text-slate-600">
        La latence est le temps total mesuré autour de l'appel provider (pas un TTFT).
        Les tokens sont comptés par découpage sur les espaces : tous les providers ne
        renvoient pas d'usage exact, donc le coût qui en découle est approché.
      </p>
    </Card>
  );
}

/* ── Performance observée en production ───────────────────────────────────── */

function ObservedTable({ observed, period, onPeriod }: {
  observed: ObservedModelPerf[];
  period: string;
  onPeriod: (p: string) => void;
}) {
  const [sortKey, setSortKey] = useState<ObsSortKey>('calls');
  const [desc, setDesc] = useState(true);

  const sorted = useMemo(() => {
    return [...observed].sort((a, b) => {
      const va = a[sortKey];
      const vb = b[sortKey];
      // Les valeurs non mesurées vont systématiquement en fin de tri.
      if (va === null && vb === null) return 0;
      if (va === null) return 1;
      if (vb === null) return -1;
      return desc ? (vb as number) - (va as number) : (va as number) - (vb as number);
    });
  }, [observed, sortKey, desc]);

  function sortBy(k: ObsSortKey) {
    if (k === sortKey) setDesc((d) => !d);
    else { setSortKey(k); setDesc(true); }
  }

  const totalCost = observed.reduce((s, m) => s + m.cost_usd, 0);
  const totalCalls = observed.reduce((s, m) => s + m.calls, 0);

  return (
    <Card
      title="Performance observée en production"
      subtitle="Ce que les modèles ont réellement fait, pas un test synthétique."
      source="GET /api/metrics/benchmarks → observed"
      actions={
        <select
          value={period}
          onChange={(e) => onPeriod(e.target.value)}
          className="rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm outline-none focus:border-sky-500"
        >
          <option value="24h">24 heures</option>
          <option value="7d">7 jours</option>
          <option value="30d">30 jours</option>
        </select>
      }
    >
      <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
        <Metric label="Modèles utilisés" value={observed.length} />
        <Metric label="Appels" value={totalCalls.toLocaleString('fr-FR')} />
        <Metric label="Coût cumulé" value={`$${totalCost.toFixed(4)}`} tone={totalCost > 1 ? 'warn' : 'neutral'} />
      </div>

      {observed.length === 0 ? (
        <EmptyState>Aucun appel enregistré sur cette période.</EmptyState>
      ) : (
        <Table>
          <thead className="bg-slate-900/80">
            <tr>
              <Th>Modèle</Th>
              <Th align="right" onClick={() => sortBy('calls')} active={sortKey === 'calls'} desc={desc}>Appels</Th>
              <Th align="right">Tokens</Th>
              <Th align="right" onClick={() => sortBy('cost_usd')} active={sortKey === 'cost_usd'} desc={desc}>Coût total</Th>
              <Th align="right" onClick={() => sortBy('avg_cost_per_call_usd')} active={sortKey === 'avg_cost_per_call_usd'} desc={desc}>Coût / appel</Th>
              <Th align="right" onClick={() => sortBy('avg_latency_ms')} active={sortKey === 'avg_latency_ms'} desc={desc}>Latence moy.</Th>
              <Th align="right" onClick={() => sortBy('elo')} active={sortKey === 'elo'} desc={desc}>Elo</Th>
              <Th align="right">Succès</Th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((m) => (
              <tr key={m.model} className="border-t border-slate-800/60 hover:bg-slate-800/30">
                <Td className="font-mono text-sky-300">{m.model}</Td>
                <Td align="right" className="font-mono text-slate-300">{m.calls.toLocaleString('fr-FR')}</Td>
                <Td align="right" className="font-mono text-slate-400">{m.total_tokens.toLocaleString('fr-FR')}</Td>
                <Td align="right" className="font-mono text-amber-300">${m.cost_usd.toFixed(4)}</Td>
                <Td align="right" className="font-mono text-slate-400">
                  {m.avg_cost_per_call_usd === null ? <Value value={null} /> : `$${m.avg_cost_per_call_usd.toFixed(6)}`}
                </Td>
                <Td align="right" className="font-mono text-slate-400"><Value value={m.avg_latency_ms} unit=" ms" digits={0} /></Td>
                <Td align="right" className="font-mono text-slate-400"><Value value={m.elo} digits={0} /></Td>
                <Td align="right" className="font-mono text-slate-400"><Value value={m.success_rate} unit=" %" digits={0} /></Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}

      <p className="mt-3 text-xs text-slate-600">
        « — » signifie <strong>jamais mesuré</strong> pour ce modèle, pas zéro. Les colonnes
        Elo et Succès restent souvent vides : le scoring Elo du moteur est indexé sur les
        <em> agents</em> (planner, executor…), pas sur les modèles — les deux tables ne se
        recoupent donc que partiellement.
      </p>
    </Card>
  );
}

/* ── Classement Elo ───────────────────────────────────────────────────────── */

function EloCard() {
  const eloQ = useQuery({ queryKey: ['elo'], queryFn: fetchElo });

  const rows = useMemo(() => {
    if (!eloQ.data) return [];
    return Object.entries(eloQ.data.scores)
      .flatMap(([name, domains]) =>
        Object.entries(domains).map(([domain, e]) => ({ name, domain, ...e })),
      )
      .sort((a, b) => b.elo - a.elo);
  }, [eloQ.data]);

  return (
    <Card
      title="Classement Elo"
      subtitle="Scores courants par agent et domaine — état actuel, le moteur ne conserve pas d'historique."
      source="GET /api/metrics/elo"
    >
      {queryPhase(eloQ) === 'loading' ? (
        <LoadingState />
      ) : queryPhase(eloQ) === 'paused' ? (
        <PausedState onRetry={() => eloQ.refetch()} />
      ) : queryPhase(eloQ) === 'error' ? (
        <ErrorState error={eloQ.error} onRetry={() => eloQ.refetch()} />
      ) : rows.length === 0 ? (
        <EmptyState>Aucun score Elo enregistré.</EmptyState>
      ) : (
        <Table>
          <thead className="bg-slate-900/80">
            <tr>
              <Th>Agent / modèle</Th>
              <Th>Domaine</Th>
              <Th align="right">Elo</Th>
              <Th align="right">Matchs</Th>
              <Th align="right">Victoires</Th>
              <Th align="right">Taux</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={`${r.name}-${r.domain}`} className="border-t border-slate-800/60 hover:bg-slate-800/30">
                <Td className="font-mono text-sky-300">{r.name}</Td>
                <Td className="text-slate-400">{r.domain}</Td>
                <Td align="right" className="font-mono font-semibold text-slate-200">{r.elo.toFixed(0)}</Td>
                <Td align="right" className="font-mono text-slate-400">{r.matches}</Td>
                <Td align="right" className="font-mono text-slate-400">{r.wins}</Td>
                <Td align="right" className="font-mono">
                  <span className={r.win_rate >= 80 ? 'text-emerald-400' : r.win_rate >= 50 ? 'text-amber-400' : 'text-red-400'}>
                    {r.win_rate.toFixed(0)} %
                  </span>
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </Card>
  );
}

/* ── Page ─────────────────────────────────────────────────────────────────── */

export default function Benchmarks() {
  const [period, setPeriod] = useState('30d');
  const benchQ = useQuery({
    queryKey: ['benchmarks', period],
    queryFn: () => fetchBenchmarks(period),
  });

  const latestRun = benchQ.data?.runs?.[0];

  return (
    <div className="mx-auto max-w-6xl pb-20">
      <PageHeader
        title="Comparatif modèles"
        description="Comparer les modèles sur leur latence, leur coût et leur comportement réel — pour choisir lesquels garder dans les tiers de la cascade."
      />

      <div className="space-y-6">
        <Explain title="À quoi sert cette page">
          <p>
            Le moteur route chaque tâche vers un <strong>tier</strong> (léger / moyen / fort), et
            chaque tier contient une liste ordonnée de modèles essayés en cascade. Cette page
            sert à décider <em>quels modèles méritent leur place</em> dans ces listes.
          </p>
          <p>
            La <strong>performance observée</strong> est l'historique réel de production : c'est
            elle qui doit guider vos arbitrages coût/vitesse. Le <strong>comparatif manuel</strong>{' '}
            sert à départager des candidats sur un prompt représentatif de votre usage.
          </p>
        </Explain>

        <RunBenchmarkCard />

        {queryPhase(benchQ) === 'loading' ? (
          <Card><LoadingState /></Card>
        ) : queryPhase(benchQ) === 'paused' ? (
          <PausedState onRetry={() => benchQ.refetch()} />
        ) : queryPhase(benchQ) === 'error' ? (
          <Card><ErrorState error={benchQ.error} onRetry={() => benchQ.refetch()} /></Card>
        ) : (
          <>
            {latestRun ? (
              <RunResults run={latestRun} />
            ) : (
              <Card title="Dernier comparatif">
                <EmptyState>
                  Aucun comparatif lancé pour l'instant. Sélectionnez des modèles ci-dessus.
                </EmptyState>
              </Card>
            )}
            <ObservedTable
              observed={benchQ.data?.observed ?? []}
              period={period}
              onPeriod={setPeriod}
            />
          </>
        )}

        <EloCard />

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Explain title="Optimiser le coût">
            <p>
              Triez la performance observée par <strong>Coût / appel</strong>. Un modèle cher en
              haut de la liste d'un tier fréquemment sollicité est votre premier levier :
              déplacez-le vers un tier supérieur, ou reléguez-le derrière une alternative
              gratuite dans <em>LLMs &amp; routage</em>.
            </p>
            <p className="flex items-center gap-1.5 text-slate-500">
              <Coins className="h-3.5 w-3.5" /> Les modèles en <code>-free</code> et{' '}
              <code>-cli</code> sont facturés 0 par le moteur.
            </p>
          </Explain>
          <Explain title="Optimiser la latence">
            <p>
              Triez par <strong>Latence moyenne</strong>. Pour l'assistant vocal, la latence prime
              sur la qualité : un modèle léger et rapide en tête du tier <code>leger</code> rend
              les réponses vocales bien plus naturelles.
            </p>
            <p className="flex items-center gap-1.5 text-slate-500">
              <Timer className="h-3.5 w-3.5" /> Le comparatif manuel mesure la latence totale,
              pas le premier token.
            </p>
          </Explain>
        </div>
      </div>
    </div>
  );
}
