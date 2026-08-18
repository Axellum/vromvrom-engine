import React, { useState, useEffect } from 'react';
import { Brain, ChevronDown, ChevronRight, Clock } from 'lucide-react';

interface ThinkingBlockProps {
  thinkingText: string;
  isStreaming?: boolean;
  durationMs?: number;
}

export const ThinkingBlock: React.FC<ThinkingBlockProps> = ({
  thinkingText,
  isStreaming = false,
  durationMs,
}) => {
  const [isExpanded, setIsExpanded] = useState(isStreaming);
  const [timer, setTimer] = useState(0);

  useEffect(() => {
    let interval: any;
    if (isStreaming) {
      interval = setInterval(() => {
        setTimer((prev) => prev + 100);
      }, 100);
    }
    return () => clearInterval(interval);
  }, [isStreaming]);

  const displayDuration = durationMs ? (durationMs / 1000).toFixed(1) : (timer / 1000).toFixed(1);

  if (!thinkingText && !isStreaming) return null;

  return (
    <div className="my-2 border border-purple-900/40 bg-purple-950/20 rounded-xl overflow-hidden text-xs transition-all">
      <button
        type="button"
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full px-3 py-2 bg-purple-950/40 hover:bg-purple-900/30 flex items-center justify-between text-purple-300 transition-colors"
      >
        <div className="flex items-center gap-2 font-medium">
          <Brain className={`w-4 h-4 text-purple-400 ${isStreaming ? 'animate-pulse' : ''}`} />
          <span>{isStreaming ? 'Pensée en cours...' : 'Processus de raisonnement'}</span>
          <span className="text-[10px] text-purple-400/70 font-mono flex items-center gap-1">
            <Clock className="w-3 h-3" />
            {displayDuration}s
          </span>
        </div>

        <div className="flex items-center gap-1 text-purple-400/80">
          <span className="text-[10px]">{isExpanded ? 'Masquer' : 'Afficher'}</span>
          {isExpanded ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
        </div>
      </button>

      {isExpanded && (
        <div className="p-3 text-purple-200/90 font-mono text-[11px] leading-relaxed whitespace-pre-wrap border-t border-purple-900/30 bg-slate-950/50 max-h-60 overflow-y-auto">
          {thinkingText}
          {isStreaming && <span className="inline-block w-1.5 h-3 bg-purple-400 animate-pulse ml-1" />}
        </div>
      )}
    </div>
  );
};
