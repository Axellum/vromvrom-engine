import React, { useState } from 'react';
import { Activity, Check, Plus, Server, X, AlertCircle } from 'lucide-react';
import { apiFetch } from '../../api/client';

export interface CustomModelFormData {
  id: string;
  provider: string;
  name: string;
  context_window: number;
  input_cost_per_m: number;
  output_cost_per_m: number;
  supports_vision: boolean;
  supports_reasoning: boolean;
}

/** Providers locaux — IDs réels de `providers` (models_registry.db) :
 *  `ollama_local` et `local` (LM Studio). Pas de colonne `is_local` :
 *  on déduit du provider_id, et on écrit `tier: "local"` à l'enregistrement. */
function isLocalProvider(provider: string): boolean {
  return provider === "ollama_local" || provider === "local";
}

/** Options du sélecteur : `value` = `providers.id` (FK upsert_model). */
const PROVIDER_OPTIONS: { id: string; label: string }[] = [
  { id: "ollama_local", label: "Ollama (Local PC)" },
  { id: "local", label: "LM Studio (Local)" },
  { id: "deepseek", label: "DeepSeek API" },
  { id: "gemini_free", label: "Gemini Free (AI Studio)" },
  { id: "openrouter", label: "OpenRouter (OpenAI-compat)" },
];

interface CustomModelModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
}

export const CustomModelModal: React.FC<CustomModelModalProps> = ({
  isOpen,
  onClose,
  onSuccess,
}) => {
  const [formData, setFormData] = useState<CustomModelFormData>({
    id: '',
    provider: 'ollama_local',
    name: '',
    context_window: 128000,
    input_cost_per_m: 0,
    output_cost_per_m: 0,
    supports_vision: false,
    supports_reasoning: false,
  });

  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{
    success: boolean;
    latencyMs?: number;
    error?: string;
  } | null>(null);

  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  if (!isOpen) return null;

  const handlePingTest = async () => {
    if (!formData.id) {
      setFormError('L\'ID du modèle est requis pour effectuer un test.');
      return;
    }
    setTesting(true);
    setTestResult(null);
    try {
      const res = await apiFetch<{ status: string; ttft_ms?: number; latency_ms?: number; error?: string }>(
        `/api/models/${encodeURIComponent(formData.id)}/ping`,
        { method: 'POST' }
      );
      if (res.status === 'success' || res.ttft_ms || res.latency_ms) {
        setTestResult({
          success: true,
          latencyMs: res.ttft_ms || res.latency_ms || 120,
        });
      } else {
        setTestResult({
          success: false,
          error: res.error || 'Aucune réponse du provider.',
        });
      }
    } catch (err: any) {
      setTestResult({
        success: false,
        error: err.message || 'Échec du test de ping',
      });
    } finally {
      setTesting(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formData.id || !formData.name) {
      setFormError('Veuillez remplir l\'ID et le nom d\'affichage.');
      return;
    }
    setSaving(true);
    setFormError(null);
    try {
      // Mapping formulaire → ModelUpdateBody (api/routes/models_admin.py:33) :
      // les noms de champs divergent (le modal avait été écrit contre une route
      // d'ajout inexistante #T289). L'unique chemin d'écriture du catalogue est
      // `POST /api/models/update` (merge-patch).
      const body = {
        id: formData.id,
        provider_id: formData.provider,
        display_name: formData.name,
        // Locaux → tier "local" (comme seed_models_db) ; sinon défaut "free".
        tier: isLocalProvider(formData.provider) ? "local" : "free",
        context_input: formData.context_window,
        cost_input_per_m: formData.input_cost_per_m,
        cost_output_per_m: formData.output_cost_per_m,
        // La base stocke supports_* en int 0/1, PAS en booléen.
        supports_vision: formData.supports_vision ? 1 : 0,
        supports_thinking: formData.supports_reasoning ? 1 : 0,
      };
      await apiFetch('/api/models/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      onSuccess();
      onClose();
    } catch (err: any) {
      setFormError(err.message || 'Erreur lors de l\'enregistrement du modèle.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="bg-slate-900 border border-slate-800 rounded-xl shadow-2xl w-full max-w-lg overflow-hidden flex flex-col">
        {/* En-tête */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-800 bg-slate-950/50">
          <div className="flex items-center gap-2 text-indigo-400">
            <Server className="w-5 h-5" />
            <h3 className="text-lg font-semibold text-slate-100">Ajouter un Modèle Personnalisé</h3>
          </div>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-200 transition-colors p-1 rounded-lg hover:bg-slate-800"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Formulaire */}
        <form onSubmit={handleSubmit} className="p-6 space-y-4 overflow-y-auto max-h-[75vh]">
          {formError && (
            <div className="p-3 bg-red-950/50 border border-red-800/80 rounded-lg flex items-start gap-2 text-red-300 text-sm">
              <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
              <span>{formError}</span>
            </div>
          )}

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">ID API du Modèle *</label>
              <input
                type="text"
                placeholder="ex: ollama/qwen2.5-coder:32b"
                value={formData.id}
                onChange={(e) => setFormData({ ...formData, id: e.target.value })}
                className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 font-mono"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">Provider Cible</label>
              <select
                value={formData.provider}
                onChange={(e) => setFormData({ ...formData, provider: e.target.value })}
                className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
              >
                {PROVIDER_OPTIONS.map((p) => (
                  <option key={p.id} value={p.id}>{p.label}</option>
                ))}
              </select>
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">Nom d'affichage *</label>
            <input
              type="text"
              placeholder="ex: Qwen 2.5 Coder 32B (Ollama Local)"
              value={formData.name}
              onChange={(e) => setFormData({ ...formData, name: e.target.value })}
              className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
            />
          </div>

          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">Fenêtre Contexte</label>
              <input
                type="number"
                value={formData.context_window}
                onChange={(e) => setFormData({ ...formData, context_window: parseInt(e.target.value) || 0 })}
                className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-200 font-mono"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">Coût In ($/1M)</label>
              <input
                type="number"
                step="0.01"
                value={formData.input_cost_per_m}
                onChange={(e) => setFormData({ ...formData, input_cost_per_m: parseFloat(e.target.value) || 0 })}
                className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-200 font-mono"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">Coût Out ($/1M)</label>
              <input
                type="number"
                step="0.01"
                value={formData.output_cost_per_m}
                onChange={(e) => setFormData({ ...formData, output_cost_per_m: parseFloat(e.target.value) || 0 })}
                className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-200 font-mono"
              />
            </div>
          </div>

          <div className="pt-2 flex flex-wrap gap-4 border-t border-slate-800/80">
            {/* Localité déduite de providers.id (ollama_local / local) — cf. isLocalProvider. */}
            <span
              className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${
                isLocalProvider(formData.provider)
                  ? 'bg-emerald-500/15 text-emerald-400'
                  : 'bg-slate-800 text-slate-400'
              }`}
              title={isLocalProvider(formData.provider)
                ? 'Le provider sélectionné est exécuté en local (GPU 0€).'
                : 'Le provider sélectionné est distant (API cloud).'}
            >
              <Server className="w-3.5 h-3.5" />
              {isLocalProvider(formData.provider) ? 'Hôte Local (GPU 0€)' : 'Distant (API cloud)'}
            </span>

            <label className="flex items-center gap-2 cursor-pointer text-xs text-slate-300">
              <input
                type="checkbox"
                checked={formData.supports_vision}
                onChange={(e) => setFormData({ ...formData, supports_vision: e.target.checked })}
                className="rounded border-slate-700 bg-slate-950 text-indigo-500 focus:ring-0"
              />
              <span>Vision (Multimodal)</span>
            </label>

            <label className="flex items-center gap-2 cursor-pointer text-xs text-slate-300">
              <input
                type="checkbox"
                checked={formData.supports_reasoning}
                onChange={(e) => setFormData({ ...formData, supports_reasoning: e.target.checked })}
                className="rounded border-slate-700 bg-slate-950 text-indigo-500 focus:ring-0"
              />
              <span>Raisonnement (Thinking)</span>
            </label>
          </div>

          <div className="pt-3 border-t border-slate-800 flex items-center justify-between">
            <button
              type="button"
              onClick={handlePingTest}
              disabled={testing || !formData.id}
              className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-medium rounded-lg flex items-center gap-1.5 transition-colors disabled:opacity-50"
            >
              <Activity className={`w-3.5 h-3.5 ${testing ? 'animate-spin text-indigo-400' : ''}`} />
              <span>{testing ? 'Test en cours...' : 'Tester la connexion (Ping)'}</span>
            </button>

            {testResult && (
              <div className="flex items-center gap-1.5 text-xs font-mono">
                {testResult.success ? (
                  <span className="text-emerald-400 flex items-center gap-1">
                    <Check className="w-3.5 h-3.5" /> TTFT: {testResult.latencyMs} ms
                  </span>
                ) : (
                  <span className="text-rose-400 flex items-center gap-1" title={testResult.error}>
                    <AlertCircle className="w-3.5 h-3.5" /> Injoignable
                  </span>
                )}
              </div>
            )}
          </div>

          <div className="pt-4 border-t border-slate-800 flex items-center justify-end gap-3">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 text-sm rounded-lg transition-colors"
            >
              Annuler
            </button>
            <button
              type="submit"
              disabled={saving}
              className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-medium rounded-lg shadow-lg shadow-indigo-600/20 flex items-center gap-2 transition-all disabled:opacity-50"
            >
              <Plus className="w-4 h-4" />
              <span>{saving ? 'Enregistrement...' : 'Enregistrer le Modèle'}</span>
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
