import { useState, useMemo, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  fetchModels,
  toggleModel,
  pingModel,
  setRoutingTier,
  setModelsStatus,
  estCandidatDesactivation,
  fetchInventaire,
  refreshInventaire,
} from "../api/models";
import type { ModelInfo, BulkStatusResult } from "../api/models";
import { CustomModelModal } from "../components/openfox_prep/CustomModelModal";

/** Libellés du sélecteur de capacité (routing_tier, cf. #T185). */
const ROUTING_TIERS: { value: "leger" | "moyen" | "fort" | ""; label: string }[] = [
  { value: "", label: "— hors routage" },
  { value: "leger", label: "Léger" },
  { value: "moyen", label: "Moyen" },
  { value: "fort", label: "Fort" },
];

function ModelCard({
  model,
  selectionne,
  onSelection,
}: {
  model: ModelInfo;
  selectionne: boolean;
  onSelection: (id: string, coche: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const [pingResult, setPingResult] = useState<{ ms?: number; err?: string } | null>(null);
  const candidat = estCandidatDesactivation(model);

  const toggleMut = useMutation({
    mutationFn: () => toggleModel(model.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["models"] }),
  });

  const tierMut = useMutation({
    mutationFn: (tier: "leger" | "moyen" | "fort" | null) => setRoutingTier(model.id, tier),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["models"] }),
  });

  const pingMut = useMutation({
    mutationFn: () => pingModel(model.id),
    onSuccess: (data) => {
      if (data.ok) setPingResult({ ms: data.latency_ms });
      else setPingResult({ err: data.error || "Erreur inconnue" });
    },
    onError: (err) => setPingResult({ err: err.message }),
  });

  const cbColors: Record<string, string> = {
    CLOSED: "bg-emerald-500/20 text-emerald-400",
    OPEN: "bg-red-500/20 text-red-400",
    HALF_OPEN: "bg-orange-500/20 text-orange-400",
  };

  const latencyColor =
    model.avg_latency_ms == null
      ? "text-slate-500"
      : model.avg_latency_ms < 500
        ? "text-emerald-400"
        : model.avg_latency_ms < 2000
          ? "text-orange-400"
          : "text-red-400";

  const eloPct =
    model.elo_score != null ? Math.min(100, Math.max(0, (model.elo_score / 2000) * 100)) : 0;

  return (
    <div className={`flex flex-col justify-between rounded-xl border bg-slate-900/40 p-4 transition hover:bg-slate-900/60 ${selectionne ? "border-sky-500" : "border-slate-800"} ${!model.enabled ? "opacity-60" : ""}`}>
      <div>
        <div className="mb-2 flex items-start justify-between gap-2">
          <label className="flex min-w-0 items-start gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={selectionne}
              onChange={(e) => onSelection(model.id, e.target.checked)}
              title="Sélectionner pour une action groupée"
              className="mt-0.5 shrink-0 rounded border-slate-700 bg-slate-900 text-sky-500 focus:ring-sky-500 focus:ring-offset-slate-900"
            />
            <h4 className="font-mono text-sm font-semibold text-sky-400 break-all">{model.name}</h4>
          </label>
          <button
            onClick={() => toggleMut.mutate()}
            disabled={toggleMut.isPending}
            title={model.enabled ? "Désactiver (retire du routage)" : "Activer"}
            className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition ${model.enabled ? "bg-sky-600" : "bg-slate-700"} disabled:opacity-50`}
          >
            <span className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition ${model.enabled ? "translate-x-4" : "translate-x-1"}`} />
          </button>
        </div>

        <div className="mb-4 flex flex-wrap items-center gap-2">
          <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] font-medium text-slate-300 uppercase" title="Canal d'accès (colonne tier)">
            {model.tier}
          </span>
          {model.circuit_breaker_status ? (
            <span className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${cbColors[model.circuit_breaker_status]}`}>
              CB: {model.circuit_breaker_status}
            </span>
          ) : (
            <span className="rounded bg-slate-800/60 px-1.5 py-0.5 text-[10px] text-slate-500" title="Aucun appel depuis le démarrage du serveur">
              CB: —
            </span>
          )}
          {!model.is_wired && (
            <span
              className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-medium text-amber-400"
              title="Au catalogue mais non câblé dans le gateway (#T186) : injoignable"
            >
              non câblé
            </span>
          )}
          {candidat && (
            <span
              className="rounded bg-red-500/15 px-1.5 py-0.5 text-[10px] font-medium text-red-400"
              title="Jamais appelé depuis l'origine ET non câblé : ne sert à rien au catalogue (#T243)"
            >
              candidat
            </span>
          )}
          {model.offered_by_api === false && (
            <span
              className="rounded bg-violet-500/15 px-1.5 py-0.5 text-[10px] font-medium text-violet-400"
              title="Ne figure pas dans le listing de son API au dernier inventaire. INDICE, pas preuve : un alias encore servi peut être absent du listing (cas vérifié de deepseek-chat)."
            >
              absent du listing
            </span>
          )}
          <select
            value={model.routing_tier ?? ""}
            onChange={(e) => tierMut.mutate((e.target.value || null) as "leger" | "moyen" | "fort" | null)}
            disabled={tierMut.isPending}
            title="Capacité de routage (routing_tier) — lue par get_provider_for_tier"
            className="ml-auto rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 text-[10px] text-slate-300 focus:border-sky-500 focus:outline-none disabled:opacity-50"
          >
            {ROUTING_TIERS.map((t) => (
              <option key={t.value} value={t.value}>{t.label}</option>
            ))}
          </select>
        </div>

        <div className="mb-3 space-y-1">
          <div className="flex justify-between text-xs text-slate-400">
            <span>ELO Score</span>
            <span className="font-mono text-slate-200">{model.elo_score ?? "—"}</span>
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
            <div className="h-full bg-gradient-to-r from-sky-600 to-sky-400" style={{ width: `${eloPct}%` }} />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-2 text-xs">
          <div className="rounded bg-slate-950 p-2">
            <span className="block text-slate-500">Latence moy.</span>
            <span className={`font-mono font-medium ${latencyColor}`}>
              {model.avg_latency_ms != null ? `${Math.round(model.avg_latency_ms)} ms` : "—"}
            </span>
          </div>
          <div className="rounded bg-slate-950 p-2">
            <span className="block text-slate-500">Coût / succès</span>
            <span className="font-mono font-medium text-emerald-400/80">
              {model.cost_per_success != null ? `$${model.cost_per_success.toFixed(4)}` : "—"}
            </span>
          </div>
          <div className="rounded bg-slate-950 p-2">
            <span className="block text-slate-500">Coût 30 j</span>
            <span className="font-mono font-medium text-slate-300">
              {model.cost_usd_30d != null ? `$${model.cost_usd_30d.toFixed(4)}` : "—"}
            </span>
          </div>
          <div className="rounded bg-slate-950 p-2">
            <span className="block text-slate-500">Appels 30 j</span>
            <span className="font-mono font-medium text-slate-300">{model.calls_30d ?? "—"}</span>
          </div>
          <div className="col-span-2 rounded bg-slate-950 p-2">
            <span className="block text-slate-500">Appels depuis toujours</span>
            <span
              className={`font-mono font-medium ${model.calls_total === 0 ? "text-slate-500" : "text-slate-300"}`}
              title="Total historique (token_usage) — le critère qui permet de décider d'une désactivation"
            >
              {model.calls_total === 0 ? "jamais appelé" : model.calls_total}
            </span>
          </div>
        </div>
      </div>

      <div className="mt-4 flex items-center justify-between border-t border-slate-800 pt-3">
        <div className="text-xs">
          {pingMut.isPending && <span className="text-slate-400 animate-pulse">Ping…</span>}
          {pingResult?.ms !== undefined && <span className="text-emerald-400">Pong : {pingResult.ms} ms</span>}
          {pingResult?.err && (
            <span className="text-red-400 truncate max-w-[160px] block" title={pingResult.err}>
              Err : {pingResult.err}
            </span>
          )}
        </div>
        <button
          onClick={() => pingMut.mutate()}
          disabled={pingMut.isPending || !model.is_wired}
          title={model.is_wired ? "Mini-génération chronométrée via le gateway" : "Modèle non câblé : ping impossible"}
          className="rounded border border-slate-700 px-3 py-1 text-xs font-medium text-slate-300 hover:bg-slate-800 disabled:opacity-50"
        >
          Ping
        </button>
      </div>
    </div>
  );
}

export function LLMRegistry() {
  const [search, setSearch] = useState("");
  const [activeOnly, setActiveOnly] = useState(true);
  const [candidatsOnly, setCandidatsOnly] = useState(false);
  const [absentsOnly, setAbsentsOnly] = useState(false);
  const [selection, setSelection] = useState<Set<string>>(new Set());
  const [bilan, setBilan] = useState<BulkStatusResult | null>(null);
  const [modalAjoutOuvert, setModalAjoutOuvert] = useState(false);
  const queryClient = useQueryClient();

  const { data: models = [], isLoading, isError, error } = useQuery({
    queryKey: ["models"],
    queryFn: fetchModels,
  });

  const { data: inventaire } = useQuery({
    queryKey: ["models-inventaire"],
    queryFn: fetchInventaire,
  });

  const inventaireMut = useMutation({
    mutationFn: refreshInventaire,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["models-inventaire"] });
      queryClient.invalidateQueries({ queryKey: ["models"] });
    },
  });

  const lotMut = useMutation({
    mutationFn: ({ ids, status }: { ids: string[]; status: "active" | "inactive" }) =>
      setModelsStatus(ids, status),
    onSuccess: (res) => {
      setBilan(res);
      setSelection(new Set());
      queryClient.invalidateQueries({ queryKey: ["models"] });
    },
  });

  const candidats = useMemo(() => models.filter(estCandidatDesactivation), [models]);

  const filteredModels = useMemo(() => {
    return models.filter((m) => {
      if (activeOnly && !m.enabled) return false;
      if (candidatsOnly && !estCandidatDesactivation(m)) return false;
      // `=== false` strictement : `null` (on ne sait pas) ne doit pas passer le filtre.
      if (absentsOnly && m.offered_by_api !== false) return false;
      if (search && !m.name.toLowerCase().includes(search.toLowerCase())) return false;
      return true;
    });
  }, [models, search, activeOnly, candidatsOnly, absentsOnly]);

  const absents = useMemo(
    () => models.filter((m) => m.offered_by_api === false).length,
    [models],
  );

  const basculerSelection = (id: string, coche: boolean) => {
    setSelection((prec) => {
      const suivant = new Set(prec);
      if (coche) suivant.add(id);
      else suivant.delete(id);
      return suivant;
    });
  };

  /** Ne sélectionne QUE ce qui est visible : cocher en aveugle des modèles masqués
   *  par la recherche serait le meilleur moyen d'en désactiver un par mégarde. */
  const selectionnerVisibles = () => setSelection(new Set(filteredModels.map((m) => m.id)));

  /** Intersection sélection ∩ filtre courant — le lot et le compteur ne portent
   *  jamais sur des cartes hors écran (un filtre changé ne doit pas laisser des
   *  ids fantômes dans l'action groupée). */
  const selectionVisible = useMemo(() => {
    const visibles = new Set(filteredModels.map((m) => m.id));
    return [...selection].filter((id) => visibles.has(id));
  }, [selection, filteredModels]);

  useEffect(() => {
    const visibles = new Set(filteredModels.map((m) => m.id));
    setSelection((prec) => {
      let change = false;
      const suivant = new Set<string>();
      for (const id of prec) {
        if (visibles.has(id)) suivant.add(id);
        else change = true;
      }
      return change ? suivant : prec;
    });
  }, [filteredModels]);

  const groupedByProvider = useMemo(() => {
    const groups: Record<string, ModelInfo[]> = {};
    filteredModels.forEach((m) => {
      if (!groups[m.provider]) groups[m.provider] = [];
      groups[m.provider].push(m);
    });
    return groups;
  }, [filteredModels]);

  if (isLoading) return <p className="text-slate-500">Chargement du registre…</p>;
  if (isError) return <p className="text-red-400">Erreur : {(error as Error).message}</p>;

  const totalActive = models.filter((m) => m.enabled).length;
  const totalWired = models.filter((m) => m.is_wired).length;
  const totalRouted = models.filter((m) => m.routing_tier).length;

  return (
    <div className="mx-auto max-w-6xl space-y-6 pb-20">
      <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-100">Registre LLM</h1>
          <p className="text-sm text-slate-400">
            Catalogue dynamique, capacité de routage (léger/moyen/fort) et Circuit Breakers
          </p>
        </div>

        <div className="flex items-center gap-3 rounded-lg border border-slate-800 bg-slate-900/50 p-2">
          <input
            type="text"
            placeholder="Rechercher…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-48 rounded bg-slate-950 px-3 py-1.5 text-sm text-slate-200 focus:outline-none focus:ring-1 focus:ring-sky-500"
          />
          <label className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer">
            <input
              type="checkbox"
              checked={activeOnly}
              onChange={(e) => setActiveOnly(e.target.checked)}
              className="rounded border-slate-700 bg-slate-900 text-sky-500 focus:ring-sky-500 focus:ring-offset-slate-900"
            />
            Actifs seulement
          </label>
          <label
            className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer"
            title="Jamais appelé depuis l'origine ET non câblé dans le gateway"
          >
            <input
              type="checkbox"
              checked={candidatsOnly}
              onChange={(e) => setCandidatsOnly(e.target.checked)}
              className="rounded border-slate-700 bg-slate-900 text-red-500 focus:ring-red-500 focus:ring-offset-slate-900"
            />
            Candidats à désactiver
            <span className="rounded-full bg-red-500/15 px-1.5 text-xs font-medium text-red-400">
              {candidats.length}
            </span>
          </label>
          <label
            className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer"
            title="Ne figure plus dans le listing de son API — indice, pas preuve"
          >
            <input
              type="checkbox"
              checked={absentsOnly}
              onChange={(e) => setAbsentsOnly(e.target.checked)}
              className="rounded border-slate-700 bg-slate-900 text-violet-500 focus:ring-violet-500 focus:ring-offset-slate-900"
            />
            Absents du listing
            <span className="rounded-full bg-violet-500/15 px-1.5 text-xs font-medium text-violet-400">
              {absents}
            </span>
          </label>
          <button
            onClick={() => setModalAjoutOuvert(true)}
            title="Ajouter un modèle personnalisé au catalogue"
            className="ml-auto rounded-lg bg-sky-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-sky-500 transition-colors"
          >
            + Ajouter un modèle
          </button>
        </div>
      </div>

      {/* Inventaire live (#T243) — état et rafraîchissement explicite : interroger une
          douzaine d'API à chaque affichage du registre serait inacceptable. */}
      <div className="flex flex-wrap items-center gap-3 rounded-lg border border-slate-800 bg-slate-900/30 px-3 py-2 text-xs">
        <span className="text-slate-400">
          Inventaire des API :{" "}
          {inventaire?.genere_le ? (
            <>
              <strong className="text-slate-200">{inventaire.providers_ok}/{inventaire.providers_total}</strong>{" "}
              provider(s) ont répondu, {inventaire.modeles_recenses} modèle(s) recensés —{" "}
              {new Date(inventaire.genere_le).toLocaleString("fr-FR")}
            </>
          ) : (
            <em className="text-slate-500">jamais généré — le signal « absent du listing » est indisponible</em>
          )}
        </span>
        <button
          onClick={() => inventaireMut.mutate()}
          disabled={inventaireMut.isPending}
          title="Interroge l'endpoint de listing de chaque provider (plusieurs secondes)"
          className="rounded border border-slate-700 px-2.5 py-1 text-slate-300 hover:bg-slate-800 disabled:opacity-40"
        >
          {inventaireMut.isPending ? "Interrogation…" : "Rafraîchir l'inventaire"}
        </button>
        {inventaireMut.isError && (
          <span className="text-red-400">Erreur : {(inventaireMut.error as Error).message}</span>
        )}
        <span className="ml-auto text-slate-500">
          « Absent du listing » est un indice, pas une preuve : un alias encore servi peut
          ne pas y figurer.
        </span>
      </div>

      {/* Barre d'action groupée (#T243) — l'interrupteur unitaire existait déjà, c'est
          l'absence d'action en lot qui rendait toute curation impraticable. */}
      <div className="flex flex-wrap items-center gap-3 rounded-lg border border-slate-800 bg-slate-900/50 p-3">
        <span className="text-sm text-slate-300">
          <strong className="text-slate-100">{selectionVisible.length}</strong>{" "}
          sélectionné{selectionVisible.length > 1 ? "s" : ""}
        </span>
        <button
          onClick={selectionnerVisibles}
          disabled={filteredModels.length === 0}
          className="rounded border border-slate-700 px-2.5 py-1 text-xs text-slate-300 hover:bg-slate-800 disabled:opacity-40"
        >
          Sélectionner les {filteredModels.length} visibles
        </button>
        <button
          onClick={() => setSelection(new Set())}
          disabled={selectionVisible.length === 0}
          className="rounded border border-slate-700 px-2.5 py-1 text-xs text-slate-300 hover:bg-slate-800 disabled:opacity-40"
        >
          Tout désélectionner
        </button>
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={() => lotMut.mutate({ ids: selectionVisible, status: "inactive" })}
            disabled={selectionVisible.length === 0 || lotMut.isPending}
            className="rounded bg-red-600/80 px-3 py-1 text-xs font-medium text-white hover:bg-red-600 disabled:opacity-40"
          >
            Désactiver la sélection
          </button>
          <button
            onClick={() => lotMut.mutate({ ids: selectionVisible, status: "active" })}
            disabled={selectionVisible.length === 0 || lotMut.isPending}
            className="rounded bg-sky-600/80 px-3 py-1 text-xs font-medium text-white hover:bg-sky-600 disabled:opacity-40"
          >
            Activer la sélection
          </button>
        </div>
        {lotMut.isPending && <span className="w-full text-xs text-slate-400 animate-pulse">Application…</span>}
        {lotMut.isError && (
          <span className="w-full text-xs text-red-400">Erreur : {(lotMut.error as Error).message}</span>
        )}
        {bilan && !lotMut.isPending && (
          <span className="w-full text-xs text-slate-400">
            {bilan.modifies.length} modifié{bilan.modifies.length > 1 ? "s" : ""} en «&nbsp;{bilan.applique}&nbsp;»
            {bilan.inchanges.length > 0 && `, ${bilan.inchanges.length} déjà dans cet état`}
            {bilan.introuvables.length > 0 && `, ${bilan.introuvables.length} introuvable(s)`}
            {bilan.echecs.length > 0 && (
              <strong className="text-red-400">, {bilan.echecs.length} échec(s)</strong>
            )}
          </span>
        )}
      </div>

      <div className="space-y-8">
        {Object.entries(groupedByProvider).map(([provider, provModels]) => (
          <section key={provider} className="space-y-4">
            <div className="flex items-center gap-3 border-b border-slate-800 pb-2">
              <h2 className="text-lg font-semibold text-slate-200 capitalize">{provider}</h2>
              <span className="rounded-full bg-slate-800 px-2 py-0.5 text-xs font-medium text-slate-400">
                {provModels.length} modèles
              </span>
            </div>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {provModels.map((model) => (
                <ModelCard
                  key={model.id}
                  model={model}
                  selectionne={selection.has(model.id)}
                  onSelection={basculerSelection}
                />
              ))}
            </div>
          </section>
        ))}
        {Object.keys(groupedByProvider).length === 0 && (
          <p className="text-center text-slate-500 py-10">Aucun modèle ne correspond aux filtres.</p>
        )}
      </div>

      <div className="mt-8 flex items-center justify-around rounded-xl border border-slate-800 bg-slate-900 p-4 text-sm">
        <div className="text-center">
          <span className="block text-slate-400">Total Modèles</span>
          <strong className="text-lg text-slate-200">{models.length}</strong>
        </div>
        <div className="text-center">
          <span className="block text-slate-400">Actifs</span>
          <strong className="text-lg text-sky-400">{totalActive}</strong>
        </div>
        <div className="text-center">
          <span className="block text-slate-400">Câblés gateway</span>
          <strong className="text-lg text-emerald-400">{totalWired}</strong>
        </div>
        <div className="text-center">
          <span className="block text-slate-400">Avec routing_tier</span>
          <strong className="text-lg text-purple-400">{totalRouted}</strong>
        </div>
      </div>

      {/* Modal d'ajout de modèle personnalisé (#T289) — sur succès, on invalide le
          catalogue pour que le modèle ajouté apparaisse sans rechargement de page. */}
      <CustomModelModal
        isOpen={modalAjoutOuvert}
        onClose={() => setModalAjoutOuvert(false)}
        onSuccess={() => queryClient.invalidateQueries({ queryKey: ["models"] })}
      />
    </div>
  );
}
