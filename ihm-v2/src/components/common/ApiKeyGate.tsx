/**
 * Porte d'authentification : tant qu'aucune tab5-engine_API_KEY n'est saisie (ou si le
 * serveur la refuse), on affiche un formulaire au lieu du dashboard. La clé n'est
 * utilisée que pour le header Bearer / la demande de ticket — jamais dans une URL.
 */
import { useState } from "react";
import { getApiKey, setApiKey } from "../../api/client";

interface Props {
  needsKey: boolean;
  onSubmit: () => void;
}

export function ApiKeyGate({ needsKey, onSubmit }: Props) {
  const [value, setValue] = useState(getApiKey());

  if (!needsKey) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm">
      <form
        className="w-full max-w-md rounded-xl border border-slate-700 bg-slate-900 p-6 shadow-2xl"
        onSubmit={(e) => {
          e.preventDefault();
          setApiKey(value);
          onSubmit();
        }}
      >
        <h2 className="mb-1 text-lg font-semibold text-slate-100">🔐 Authentification du moteur</h2>
        <p className="mb-4 text-sm text-slate-400">
          {/* Le nom affiché DOIT être celui que le backend lit réellement : `require_auth`
              et `api/routes/setup.py` lisent `MOTEUR_API_KEY`. L'écran annonçait
              `tab5-engine_API_KEY`, une variable qui n'existe nulle part côté moteur —
              premier écran vu par un nouvel utilisateur, il l'envoyait chercher un
              réglage inexistant. */}
          Saisis la <code className="text-slate-300">MOTEUR_API_KEY</code> (header Bearer). Elle reste
          locale à ce navigateur et n'apparaît jamais dans une URL.
        </p>
        <input
          type="password"
          autoFocus
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="MOTEUR_API_KEY"
          className="w-full rounded-lg border border-slate-600 bg-slate-800 px-3 py-2 text-slate-100 outline-none focus:border-sky-500"
        />
        <button
          type="submit"
          disabled={!value.trim()}
          className="mt-4 w-full rounded-lg bg-sky-600 px-4 py-2 font-medium text-white transition hover:bg-sky-500 disabled:opacity-40"
        >
          Connecter
        </button>
      </form>
    </div>
  );
}
