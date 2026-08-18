import React, { useState } from 'react';
import { Shield, ShieldAlert, ShieldCheck, Filter, Wrench, ChevronDown, ChevronRight } from 'lucide-react';

export type PermissionMode = 'all' | 'none' | 'partial';

export interface MCPToolGroup {
  serverId: string;
  serverName: string;
  tools: {
    name: string;
    description: string;
  }[];
}

export const AVAILABLE_MCP_GROUPS: MCPToolGroup[] = [
  {
    serverId: 'system',
    serverName: 'Système & Fichiers',
    tools: [
      { name: 'read_file', description: 'Lecture de fichiers dans le workspace' },
      { name: 'write_file', description: 'Écriture et édition de fichiers' },
      { name: 'run_terminal_command', description: 'Exécution de commandes terminal sandboxées' },
      // [#T290] « git_safety » était un alias conceptuel : aucun outil de ce nom
      // n'existe dans le registre (tools/registry_setup.py:74-76). Les vrais noms
      // sont les trois checkpoints Git ci-dessous — un nom divergent n'autoriserait RIEN.
      { name: 'git_create_checkpoint', description: 'Crée un checkpoint de sécurité Git' },
      { name: 'git_rollback_checkpoint', description: 'Annule des modifications via Git (rollback)' },
      { name: 'git_apply_checkpoint', description: 'Valide un checkpoint Git en fusionnant' },
    ],
  },
  {
    serverId: 'tab5',
    serverName: 'MCP Tab5 Engine',
    tools: [
      { name: 'execute_ha_action', description: 'Exécution d\'action domotique HA' },
      { name: 'search_ha_entities', description: 'Recherche d\'entités domotiques' },
      { name: 'query_llm_direct', description: 'Appel direct au hub LLM' },
      { name: 'rag_search', description: 'Recherche RAG vectorielle dans memory.db' },
    ],
  },
  {
    serverId: 'workspace',
    serverName: 'Google Workspace MCP',
    tools: [
      { name: 'get_calendar_events', description: 'Lecture du calendrier Google' },
      { name: 'get_tasks', description: 'Gestion des tâches Google Tasks' },
      { name: 'search_gmail', description: 'Recherche dans les emails Gmail' },
    ],
  },
  {
    serverId: 'memory',
    serverName: 'Mémoire Graphe & Épisodique',
    tools: [
      { name: 'create_entities', description: 'Ajout de faits dans le graphe relationnel' },
      { name: 'read_graph', description: 'Consultation du graphe de mémoire' },
      { name: 'add_observations', description: 'Enregistrement d\'observations' },
    ],
  },
];

interface ToolPermissionSelectorProps {
  allowedTools: string[] | null;
  onChange: (newAllowedTools: string[] | null) => void;
}

export const ToolPermissionSelector: React.FC<ToolPermissionSelectorProps> = ({
  allowedTools,
  onChange,
}) => {
  const getInitialMode = (): PermissionMode => {
    if (allowedTools === null) return 'all';
    if (Array.isArray(allowedTools) && allowedTools.length === 0) return 'none';
    return 'partial';
  };

  const [mode, setMode] = useState<PermissionMode>(getInitialMode());
  const [selectedTools, setSelectedTools] = useState<Set<string>>(
    new Set(Array.isArray(allowedTools) ? allowedTools : [])
  );

  const [searchFilter, setSearchFilter] = useState('');
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());

  const handleModeChange = (newMode: PermissionMode) => {
    setMode(newMode);
    if (newMode === 'all') {
      onChange(null);
    } else if (newMode === 'none') {
      onChange([]);
    } else {
      onChange(Array.from(selectedTools));
    }
  };

  const toggleTool = (toolName: string) => {
    const next = new Set(selectedTools);
    if (next.has(toolName)) {
      next.delete(toolName);
    } else {
      next.add(toolName);
    }
    setSelectedTools(next);
    onChange(Array.from(next));
  };

  const toggleGroup = (group: MCPToolGroup) => {
    const allGroupTools = group.tools.map((t) => t.name);
    const hasAll = allGroupTools.every((t) => selectedTools.has(t));

    const next = new Set(selectedTools);
    if (hasAll) {
      allGroupTools.forEach((t) => next.delete(t));
    } else {
      allGroupTools.forEach((t) => next.add(t));
    }
    setSelectedTools(next);
    onChange(Array.from(next));
  };

  const toggleCollapseGroup = (serverId: string) => {
    const next = new Set(collapsedGroups);
    if (next.has(serverId)) next.delete(serverId);
    else next.add(serverId);
    setCollapsedGroups(next);
  };

  return (
    <div className="bg-slate-950 border border-slate-800 rounded-xl p-4 space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-slate-200">
          <Shield className="w-4 h-4 text-indigo-400" />
          <span className="text-sm font-semibold">Permissions d'Outils (Moindre Privilège)</span>
        </div>
        <span className="text-xs text-slate-500 font-mono">#T233 / #T290</span>
      </div>

      <div className="grid grid-cols-3 gap-2 p-1 bg-slate-900 rounded-lg border border-slate-800/80">
        <button
          type="button"
          onClick={() => handleModeChange('all')}
          className={`px-3 py-2 rounded-md text-xs font-medium flex items-center justify-center gap-2 transition-all ${
            mode === 'all'
              ? 'bg-indigo-600 text-white shadow-md'
              : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
          }`}
        >
          <ShieldCheck className="w-3.5 h-3.5" />
          <span>Tous (`all`)</span>
        </button>

        <button
          type="button"
          onClick={() => handleModeChange('none')}
          className={`px-3 py-2 rounded-md text-xs font-medium flex items-center justify-center gap-2 transition-all ${
            mode === 'none'
              ? 'bg-rose-600 text-white shadow-md'
              : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
          }`}
        >
          <ShieldAlert className="w-3.5 h-3.5" />
          <span>Aucun (`none`)</span>
        </button>

        <button
          type="button"
          onClick={() => handleModeChange('partial')}
          className={`px-3 py-2 rounded-md text-xs font-medium flex items-center justify-center gap-2 transition-all ${
            mode === 'partial'
              ? 'bg-amber-600 text-white shadow-md'
              : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
          }`}
        >
          <Wrench className="w-3.5 h-3.5" />
          <span>Granulaire (`partial`)</span>
        </button>
      </div>

      {mode === 'partial' && (
        <div className="space-y-3 pt-2 border-t border-slate-800/80">
          <div className="relative">
            <Filter className="w-3.5 h-3.5 absolute left-3 top-2.5 text-slate-500" />
            <input
              type="text"
              placeholder="Filtrer les outils..."
              value={searchFilter}
              onChange={(e) => setSearchFilter(e.target.value)}
              className="w-full bg-slate-900 border border-slate-800 rounded-lg pl-9 pr-3 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-indigo-500"
            />
          </div>

          <div className="space-y-3 max-h-60 overflow-y-auto pr-1">
            {AVAILABLE_MCP_GROUPS.map((group) => {
              const filteredTools = group.tools.filter(
                (t) =>
                  t.name.toLowerCase().includes(searchFilter.toLowerCase()) ||
                  t.description.toLowerCase().includes(searchFilter.toLowerCase())
              );

              if (filteredTools.length === 0) return null;

              const isCollapsed = collapsedGroups.has(group.serverId);
              const selectedCount = group.tools.filter((t) => selectedTools.has(t.name)).length;

              return (
                <div key={group.serverId} className="border border-slate-800 rounded-lg overflow-hidden bg-slate-900/40">
                  <div className="px-3 py-2 bg-slate-900 flex items-center justify-between border-b border-slate-800/60">
                    <button
                      type="button"
                      onClick={() => toggleCollapseGroup(group.serverId)}
                      className="flex items-center gap-2 text-xs font-semibold text-slate-300 hover:text-white"
                    >
                      {isCollapsed ? <ChevronRight className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
                      <span>{group.serverName}</span>
                      <span className="text-[10px] text-slate-500 font-mono">
                        ({selectedCount}/{group.tools.length})
                      </span>
                    </button>

                    <button
                      type="button"
                      onClick={() => toggleGroup(group)}
                      className="text-[11px] text-indigo-400 hover:text-indigo-300 font-medium"
                    >
                      {selectedCount === group.tools.length ? 'Tout désélectionner' : 'Tout sélectionner'}
                    </button>
                  </div>

                  {!isCollapsed && (
                    <div className="p-2 grid grid-cols-1 md:grid-cols-2 gap-2">
                      {filteredTools.map((tool) => {
                        const isChecked = selectedTools.has(tool.name);
                        return (
                          <label
                            key={tool.name}
                            className={`flex items-start gap-2.5 p-2 rounded-lg border cursor-pointer transition-all ${
                              isChecked
                                ? 'bg-indigo-950/30 border-indigo-800/60 text-slate-200'
                                : 'bg-slate-950/40 border-slate-850/40 text-slate-400 hover:border-slate-700'
                            }`}
                          >
                            <input
                              type="checkbox"
                              checked={isChecked}
                              onChange={() => toggleTool(tool.name)}
                              className="mt-0.5 rounded border-slate-700 bg-slate-900 text-indigo-500 focus:ring-0"
                            />
                            <div className="text-xs">
                              <span className="font-mono font-medium text-indigo-300">{tool.name}</span>
                              <p className="text-[11px] text-slate-500 line-clamp-1">{tool.description}</p>
                            </div>
                          </label>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
};
