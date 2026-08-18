import React, { useState } from 'react';
import { CheckCircle2, XCircle, Loader2, ChevronDown, ChevronRight, Copy, Check } from 'lucide-react';

// [#T291] Le type vit dans la couche état (`state/chatStore`), source unique :
// le store le remplit depuis le SSE, ce composant ne fait que l'afficher.
import type { ToolCallData } from '../../state/chatStore';

export type { ToolCallData };

interface ToolCallDisplayProps {
  toolCall: ToolCallData;
}

export const ToolCallDisplay: React.FC<ToolCallDisplayProps> = ({ toolCall }) => {
  const [isExpanded, setIsExpanded] = useState(false);
  const [copied, setCopied] = useState(false);

  const handleCopyArgs = (e: React.MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard.writeText(JSON.stringify(toolCall.args, null, 2));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="my-2 border border-slate-800 rounded-xl overflow-hidden text-xs bg-slate-950/80 shadow-md">
      <div
        onClick={() => setIsExpanded(!isExpanded)}
        className="px-3.5 py-2.5 bg-slate-900/80 hover:bg-slate-900 flex items-center justify-between cursor-pointer transition-colors"
      >
        <div className="flex items-center gap-2.5">
          {toolCall.status === 'running' && <Loader2 className="w-4 h-4 text-indigo-400 animate-spin" />}
          {toolCall.status === 'success' && <CheckCircle2 className="w-4 h-4 text-emerald-400" />}
          {toolCall.status === 'error' && <XCircle className="w-4 h-4 text-rose-400" />}

          <span className="font-mono font-semibold text-indigo-300">{toolCall.toolName}</span>

          <span className="text-[11px] text-slate-500 truncate max-w-xs font-mono">
            {JSON.stringify(toolCall.args)}
          </span>
        </div>

        <div className="flex items-center gap-2 text-slate-400">
          {toolCall.executionTimeMs && (
            <span className="text-[10px] text-slate-500 font-mono">{toolCall.executionTimeMs} ms</span>
          )}

          <button
            type="button"
            onClick={handleCopyArgs}
            className="p-1 hover:text-slate-200 transition-colors"
            title="Copier les arguments"
          >
            {copied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
          </button>

          {isExpanded ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
        </div>
      </div>

      {isExpanded && (
        <div className="p-3 border-t border-slate-800 space-y-2 bg-slate-950 font-mono text-[11px]">
          <div>
            <span className="text-[10px] uppercase tracking-wider text-slate-500 block mb-1">Arguments de l'Outil</span>
            <pre className="p-2 bg-slate-900 rounded-lg text-amber-200/90 overflow-x-auto border border-slate-800">
              {JSON.stringify(toolCall.args, null, 2)}
            </pre>
          </div>

          {/* `Boolean(...)` : `resultData` est typé `unknown`, la chaîne `&&`
              rendrait sinon un `unknown` que JSX refuse. Même condition qu'avant. */}
          {toolCall.status === 'success' && Boolean(toolCall.resultData) && (
            <div>
              <span className="text-[10px] uppercase tracking-wider text-emerald-500 block mb-1">Résultat Renvoyé</span>
              <pre className="p-2 bg-slate-900 rounded-lg text-emerald-300/90 overflow-x-auto border border-slate-800 max-h-48">
                {typeof toolCall.resultData === 'string'
                  ? toolCall.resultData
                  : JSON.stringify(toolCall.resultData, null, 2)}
              </pre>
            </div>
          )}

          {toolCall.status === 'error' && toolCall.errorMessage && (
            <div>
              <span className="text-[10px] uppercase tracking-wider text-rose-500 block mb-1">Message d'Erreur</span>
              <div className="p-2 bg-rose-950/30 border border-rose-900/50 rounded-lg text-rose-300">
                {toolCall.errorMessage}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
};
