/**
 * Vue « Domotique & Vocal ».
 *
 * Nouvelle section : le moteur pilote Home Assistant et sert d'assistant vocal
 * au Tab5, mais aucun écran ne permettait de vérifier ni de régler cette chaîne.
 * Tout ici repose sur des appels réels (`/api/ha/health` interroge vraiment HA)
 * — plus de pastille « connecté » décorative.
 */
import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Home, Mic, Search, Power, RefreshCw, ShieldAlert, Volume2, StopCircle,
} from 'lucide-react';
import {
  fetchHaHealth, fetchHaEntities, controlHaEntity,
  fetchVocalStats, fetchVocalAudit, fetchVocalConfig, abortVocal,
  type HaEntity,
} from '../api/ha';
import {
  PageHeader, Card, Explain, Warn, Table, Th, Td, Value, Button, Metric, Pill,
  LoadingState, ErrorState, EmptyState, PausedState, queryPhase,
} from '../components/ui/primitives';

/* ── Santé de la liaison HA ───────────────────────────────────────────────── */

function HaHealthCard() {
  const q = useQuery({ queryKey: ['ha', 'health'], queryFn: fetchHaHealth, refetchInterval: 30_000 });
  const phase = queryPhase(q);

  return (
    <Card
      title="Liaison Home Assistant"
      subtitle="Testée en direct à chaque rafraîchissement — ce n'est pas un état déclaratif."
      source="GET /api/ha/health"
      actions={
        <Button variant="secondary" onClick={() => q.refetch()} disabled={q.isFetching}>
          <RefreshCw className={`h-4 w-4 ${q.isFetching ? 'animate-spin' : ''}`} />
          Tester
        </Button>
      }
    >
      {phase === 'loading' ? (
        <LoadingState label="Interrogation de Home Assistant…" />
      ) : phase === 'paused' ? (
        <PausedState onRetry={() => q.refetch()} />
      ) : phase === 'error' ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : q.data ? (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Metric
              label="Joignable"
              value={q.data.reachable ? 'oui' : 'non'}
              tone={q.data.reachable ? 'good' : 'bad'}
            />
            <Metric label="Latence" value={<Value value={q.data.latency_ms} unit=" ms" digits={0} />} />
            <Metric label="Entités" value={<Value value={q.data.entity_count} />} />
            <Metric label="Version HA" value={<Value value={q.data.version} />} />
          </div>

          <div className="space-y-2 rounded-lg border border-slate-800 bg-slate-950/50 p-4 text-sm">
            <Row label="URL"><code className="text-sky-300">{q.data.url}</code></Row>
            <Row label="Token">
              {q.data.token_configured
                ? <Pill tone="good">configuré</Pill>
                : <Pill tone="bad">absent du .env</Pill>}
            </Row>
            <Row label="Vérification TLS">
              {q.data.verify_tls ? <Pill tone="good">activée</Pill> : <Pill tone="warn">désactivée</Pill>}
            </Row>
            {q.data.ca_bundle && <Row label="CA bundle"><code className="text-xs">{q.data.ca_bundle}</code></Row>}
            {q.data.location_name && <Row label="Domicile">{q.data.location_name}</Row>}
          </div>

          {q.data.error && (
            <div className="rounded-lg border border-red-900/60 bg-red-950/30 px-4 py-3">
              <div className="flex items-start gap-2">
                <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-red-400" />
                <div className="min-w-0">
                  <p className="text-sm font-medium text-red-300">Home Assistant injoignable</p>
                  <p className="mt-1 break-words font-mono text-xs text-red-400/90">{q.data.error}</p>
                  {q.data.error.includes('CERTIFICATE_VERIFY_FAILED') && (
                    <div className="mt-3 space-y-1.5 text-xs text-slate-400">
                      <p className="font-medium text-slate-300">Comment corriger</p>
                      <p>
                        Le certificat de HA ne couvre pas l'adresse IP utilisée. Dans le{' '}
                        <code>.env</code> du moteur, au choix :
                      </p>
                      <ul className="list-inside list-disc space-y-1">
                        <li>
                          <code>HA_CA_BUNDLE=/chemin/vers/ca.pem</code> — la bonne solution,
                          garde la vérification TLS active.
                        </li>
                        <li>
                          <code>HA_VERIFY_TLS=false</code> — accepté sur un LAN de confiance,
                          mais désactive toute vérification.
                        </li>
                        <li>
                          Utiliser le nom d'hôte couvert par le certificat plutôt que l'IP
                          dans <code>HASS_URL</code>.
                        </li>
                      </ul>
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}

          {q.data.reachable && Object.keys(q.data.domains).length > 0 && (
            <div>
              <p className="mb-2 text-xs text-slate-500">
                Périmètre exposé au moteur, par domaine
              </p>
              <div className="flex flex-wrap gap-1.5">
                {Object.entries(q.data.domains).slice(0, 24).map(([domain, count]) => (
                  <span
                    key={domain}
                    className="rounded-md border border-slate-700 px-2 py-0.5 font-mono text-xs text-slate-400"
                  >
                    {domain} <span className="text-slate-600">{count}</span>
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      ) : null}
    </Card>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-slate-500">{label}</span>
      <span className="min-w-0 truncate text-right text-slate-300">{children}</span>
    </div>
  );
}

/* ── Explorateur d'entités ────────────────────────────────────────────────── */

const CONTROLLABLE = new Set(['light', 'switch', 'fan', 'input_boolean', 'automation', 'script']);

function EntitiesCard() {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState('');
  const [domain, setDomain] = useState('');
  const [confirming, setConfirming] = useState<{ entity: HaEntity; service: string } | null>(null);

  const q = useQuery({
    queryKey: ['ha', 'entities', search, domain],
    queryFn: () => fetchHaEntities({ search, domain, limit: 200 }),
  });
  const phase = queryPhase(q);

  const control = useMutation({
    mutationFn: (input: { entity_id: string; service: string }) => controlHaEntity(input),
    onSuccess: () => {
      setConfirming(null);
      void queryClient.invalidateQueries({ queryKey: ['ha', 'entities'] });
    },
  });

  return (
    <Card
      title="Entités Home Assistant"
      subtitle="Le périmètre exact sur lequel le moteur et l'assistant vocal peuvent agir."
      source="GET /api/ha/entities · POST /api/ha/control"
    >
      <div className="space-y-4">
        <div className="flex flex-wrap gap-2">
          <div className="relative min-w-56 flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-600" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Rechercher (nom ou entity_id)…"
              className="w-full rounded-lg border border-slate-700 bg-slate-950 py-2 pl-9 pr-3 text-sm outline-none focus:border-sky-500"
            />
          </div>
          <input
            value={domain}
            onChange={(e) => setDomain(e.target.value)}
            placeholder="domaine (light, sensor…)"
            className="w-48 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm outline-none focus:border-sky-500"
          />
        </div>

        {phase === 'loading' ? (
          <LoadingState />
        ) : phase === 'paused' ? (
          <PausedState onRetry={() => q.refetch()} />
        ) : phase === 'error' ? (
          <ErrorState error={q.error} onRetry={() => q.refetch()} />
        ) : q.data!.entities.length === 0 ? (
          <EmptyState>Aucune entité ne correspond.</EmptyState>
        ) : (
          <>
            <p className="text-xs text-slate-500">
              {q.data!.returned} affichée(s) sur {q.data!.total_matched} correspondance(s)
            </p>
            <div className="max-h-96 overflow-y-auto">
              <Table>
                <thead className="sticky top-0 bg-slate-900">
                  <tr>
                    <Th>Entité</Th>
                    <Th>Nom</Th>
                    <Th>État</Th>
                    <Th align="right">Action</Th>
                  </tr>
                </thead>
                <tbody>
                  {q.data!.entities.map((e) => {
                    const dom = e.entity_id.split('.')[0];
                    const canControl = CONTROLLABLE.has(dom);
                    const isOn = e.state === 'on';
                    return (
                      <tr key={e.entity_id} className="border-t border-slate-800/60 hover:bg-slate-800/30">
                        <Td className="font-mono text-xs text-sky-300">{e.entity_id}</Td>
                        <Td className="text-slate-400">{e.friendly_name || '—'}</Td>
                        <Td className="font-mono text-slate-300">
                          {e.state}
                          {e.unit && <span className="ml-0.5 text-slate-500">{e.unit}</span>}
                        </Td>
                        <Td align="right">
                          {canControl && (
                            <button
                              onClick={() => setConfirming({ entity: e, service: isOn ? 'turn_off' : 'turn_on' })}
                              className="inline-flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-xs text-slate-300 transition hover:border-sky-600 hover:text-sky-300"
                            >
                              <Power className="h-3 w-3" />
                              {isOn ? 'Éteindre' : 'Allumer'}
                            </button>
                          )}
                        </Td>
                      </tr>
                    );
                  })}
                </tbody>
              </Table>
            </div>
          </>
        )}

        {confirming && (
          <Warn>
            <div className="space-y-2">
              <p>
                Cette action agit <strong>réellement sur votre domicile</strong> :{' '}
                <code>{confirming.service}</code> sur{' '}
                <code>{confirming.entity.entity_id}</code>.
              </p>
              <div className="flex gap-2">
                <Button
                  onClick={() =>
                    control.mutate({
                      entity_id: confirming.entity.entity_id,
                      service: confirming.service,
                    })
                  }
                  disabled={control.isPending}
                >
                  {control.isPending ? 'Envoi…' : 'Confirmer'}
                </Button>
                <Button variant="ghost" onClick={() => setConfirming(null)}>Annuler</Button>
              </div>
            </div>
          </Warn>
        )}
        {control.isError && <ErrorState error={control.error} />}
      </div>
    </Card>
  );
}

/* ── Assistant vocal ──────────────────────────────────────────────────────── */

function VocalStatsCard() {
  const [period, setPeriod] = useState('7d');
  const q = useQuery({
    queryKey: ['vocal', 'stats', period],
    queryFn: () => fetchVocalStats(period),
    refetchInterval: 30_000,
  });
  const abort = useMutation({ mutationFn: abortVocal });
  const phase = queryPhase(q);

  return (
    <Card
      title="Pipeline vocal"
      subtitle="Mesures réelles du trajet STT → moteur → TTS, enregistrées à chaque échange."
      source="GET /api/vocal/stats"
      actions={
        <div className="flex items-center gap-2">
          <select
            value={period}
            onChange={(e) => setPeriod(e.target.value)}
            className="rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm outline-none focus:border-sky-500"
          >
            <option value="1h">1 heure</option>
            <option value="24h">24 heures</option>
            <option value="7d">7 jours</option>
            <option value="30d">30 jours</option>
          </select>
          <Button variant="danger" onClick={() => abort.mutate()} disabled={abort.isPending}>
            <StopCircle className="h-4 w-4" />
            Barge-in
          </Button>
        </div>
      }
    >
      {phase === 'loading' ? (
        <LoadingState />
      ) : phase === 'paused' ? (
        <PausedState onRetry={() => q.refetch()} />
      ) : phase === 'error' ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : q.data ? (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Metric label="Échanges" value={q.data.total_exchanges} />
            <Metric
              label="Latence moyenne"
              value={<Value value={q.data.latency_ms.avg} unit=" ms" digits={0} />}
              tone={
                q.data.latency_ms.avg === null ? 'neutral'
                : q.data.latency_ms.avg > 4000 ? 'bad'
                : q.data.latency_ms.avg > 2000 ? 'warn' : 'good'
              }
              hint="au-delà de 2 s, l'échange paraît lent"
            />
            <Metric label="Plus rapide" value={<Value value={q.data.latency_ms.min} unit=" ms" digits={0} />} />
            <Metric label="Plus lente" value={<Value value={q.data.latency_ms.max} unit=" ms" digits={0} />} />
          </div>

          {q.data.total_exchanges === 0 ? (
            <EmptyState>Aucun échange vocal enregistré sur cette période.</EmptyState>
          ) : (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <Breakdown title="Par mode" rows={q.data.by_mode.map((m) => ({
                key: m.source_mode ?? 'inconnu', n: m.n,
                extra: m.avg_ms !== null ? `${Math.round(m.avg_ms)} ms` : null,
              }))} />
              <Breakdown title="Par appareil" rows={q.data.by_device.map((d) => ({ key: d.device_id, n: d.n, extra: null }))} />
              <Breakdown title="Par routage" rows={q.data.by_routing.map((r) => ({ key: r.routing_type, n: r.n, extra: null }))} />
            </div>
          )}

          {abort.isSuccess && (
            <p className="text-xs text-emerald-400">
              {abort.data?.aborted ?? 0} flux vocal(aux) interrompu(s).
            </p>
          )}
          {abort.isError && <ErrorState error={abort.error} />}
        </div>
      ) : null}
    </Card>
  );
}

function Breakdown({ title, rows }: {
  title: string;
  rows: Array<{ key: string; n: number; extra: string | null }>;
}) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-950/50 p-3">
      <p className="mb-2 text-xs font-medium text-slate-400">{title}</p>
      {rows.length === 0 ? (
        <p className="text-xs text-slate-600">—</p>
      ) : (
        <div className="space-y-1">
          {rows.slice(0, 6).map((r) => (
            <div key={r.key} className="flex items-baseline justify-between gap-2 text-xs">
              <span className="truncate font-mono text-slate-400">{r.key}</span>
              <span className="shrink-0 text-slate-300">
                {r.n}
                {/* Séparateur explicite : sans lui, « 16 » et « 1493 ms » se lisaient « 161493 ms ». */}
                {r.extra && <span className="ml-1.5 text-slate-600">· {r.extra}</span>}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function VocalAuditCard() {
  const q = useQuery({ queryKey: ['vocal', 'audit'], queryFn: () => fetchVocalAudit(40), refetchInterval: 20_000 });
  const phase = queryPhase(q);

  return (
    <Card
      title="Journal des échanges vocaux"
      subtitle="Ce que le STT a transmis au moteur, et ce que le moteur a répondu — pour diagnostiquer les incompréhensions."
      source="GET /api/vocal/audit"
    >
      {phase === 'loading' ? (
        <LoadingState />
      ) : phase === 'paused' ? (
        <PausedState onRetry={() => q.refetch()} />
      ) : phase === 'error' ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : q.data!.logs.length === 0 ? (
        <EmptyState>Aucun échange vocal enregistré.</EmptyState>
      ) : (
        <div className="max-h-96 space-y-2 overflow-y-auto">
          {q.data!.logs.map((log) => (
            <div key={log.id} className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
              <div className="flex flex-wrap items-center gap-2 text-[11px]">
                <Pill tone={log.phase === 'response' ? 'good' : 'neutral'}>{log.phase}</Pill>
                {log.source_mode && <span className="font-mono text-slate-500">{log.source_mode}</span>}
                {log.device_id && <span className="font-mono text-violet-300">{log.device_id}</span>}
                {log.routing_type && <span className="text-slate-600">{log.routing_type}</span>}
                {log.latency_ms !== null && (
                  <span className="text-sky-400">{Math.round(log.latency_ms)} ms</span>
                )}
                {log.tts_enabled === 1 && <Volume2 className="h-3 w-3 text-slate-500" />}
                <span className="ml-auto text-slate-600">{log.created_at}</span>
              </div>
              {log.user_prompt && (
                <p className="mt-1.5 text-sm text-slate-300">
                  <span className="text-slate-600">« </span>{log.user_prompt}<span className="text-slate-600"> »</span>
                </p>
              )}
              {log.response_text && (
                <p className="mt-1 line-clamp-3 text-xs text-slate-500">→ {log.response_text}</p>
              )}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

function VocalConfigCard() {
  const q = useQuery({ queryKey: ['vocal', 'config'], queryFn: fetchVocalConfig });
  const phase = queryPhase(q);

  return (
    <Card
      title="Configuration vocale effective"
      subtitle="Ce que le moteur lit réellement à l'exécution."
      source="GET /api/vocal/config"
    >
      {phase === 'loading' ? (
        <LoadingState />
      ) : phase === 'paused' ? (
        <PausedState onRetry={() => q.refetch()} />
      ) : phase === 'error' ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : q.data ? (
        <div className="space-y-2 text-sm">
          <Row label="Modèle de l'agent HA"><code className="text-sky-300">{q.data.ha_model ?? '—'}</code></Row>
          <Row label="Modèle de l'Executor"><code className="text-sky-300">{q.data.executor_model ?? '—'}</code></Row>
          <Row label="Cache sémantique">
            {q.data.semantic_cache?.enabled
              ? <Pill tone="good">activé</Pill>
              : <Pill tone="neutral">désactivé</Pill>}
          </Row>
          <Row label="TTS cloud">
            {q.data.tts_cloud_available
              ? <Pill tone="good">disponible</Pill>
              : <Pill tone="warn">aucune clé Gemini</Pill>}
          </Row>
          <div className="mt-3 border-t border-slate-800 pt-3">
            <p className="mb-2 text-xs text-slate-500">Secrets requis (présence uniquement)</p>
            <div className="flex flex-wrap gap-2">
              {Object.entries(q.data.secrets_present).map(([name, present]) => (
                <Pill key={name} tone={present ? 'good' : 'bad'}>
                  {name} {present ? '✓' : '✗'}
                </Pill>
              ))}
            </div>
          </div>
          <p className="mt-2 text-xs text-slate-600">
            Le modèle de l'agent HA se change dans <strong>Configuration → Modèles &amp; routage</strong>.
            Pour le vocal, privilégiez un tier <code>leger</code> : la latence prime sur la finesse
            du raisonnement.
          </p>
        </div>
      ) : null}
    </Card>
  );
}

/* ── Page ─────────────────────────────────────────────────────────────────── */

export function HomeAssistantView() {
  return (
    <div className="mx-auto max-w-6xl pb-20">
      <PageHeader
        title="Domotique & Vocal"
        description="La liaison entre le moteur, Home Assistant et l'assistant vocal du Tab5 — vérifier qu'elle fonctionne, comprendre ce qui passe, et la régler."
      />

      <div className="space-y-6">
        <Explain title="Comment le vocal traverse le moteur">
          <p>
            Le Tab5 capte la parole et la transcrit (STT), puis envoie le texte à{' '}
            <code>/api/execute</code> avec une source <code>tab5</code>. Le routeur classe la
            demande : une commande domotique part vers l'agent HA, une question ouverte vers
            le mode Discussion. La réponse revient, et le Tab5 la vocalise (TTS).
          </p>
          <p>
            Chaque étape est journalisée dans <code>vocal_audit_log</code> — c'est ce journal
            qui alimente les mesures ci-dessous. Quand une commande vocale « ne marche pas »,
            le journal montre exactement ce que le STT a compris.
          </p>
        </Explain>

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <div className="space-y-6">
            <div className="flex items-center gap-2 text-sm font-medium text-slate-400">
              <Home className="h-4 w-4" /> Home Assistant
            </div>
            <HaHealthCard />
          </div>
          <div className="space-y-6">
            <div className="flex items-center gap-2 text-sm font-medium text-slate-400">
              <Mic className="h-4 w-4" /> Assistant vocal
            </div>
            <VocalConfigCard />
          </div>
        </div>

        <VocalStatsCard />
        <EntitiesCard />
        <VocalAuditCard />
      </div>
    </div>
  );
}

export default HomeAssistantView;
