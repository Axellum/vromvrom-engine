import { useState, useCallback, useEffect, useRef } from 'react';
import {
  ReactFlow,
  ReactFlowProvider,
  addEdge,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  Handle,
  Position,
  Panel
} from '@xyflow/react';
import type { Connection, Edge, Node } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useQuery, useMutation } from '@tanstack/react-query';
import { fetchWorkflow, saveWorkflow } from '../api/workflows';
import type { WorkflowNodeData } from '../api/workflows';
import { Save, RotateCcw, Upload, Download, Settings2 } from 'lucide-react';

// --- Custom Node ---
const AgentNode = ({ data, selected }: { data: WorkflowNodeData; selected: boolean }) => {
  return (
    <div className={`px-4 py-3 shadow-lg rounded-xl border-2 bg-slate-800 text-slate-200 min-w-[150px] transition-colors ${selected ? 'border-sky-500 shadow-sky-900/20' : 'border-slate-600'}`}>
      <Handle type="target" position={Position.Top} className="w-3 h-3 bg-sky-400" />
      <div className="flex flex-col items-center">
        <div className="font-bold text-sm">{data.label}</div>
        <div className="text-xs text-slate-400 mt-1">{data.agent}</div>
        <div className="mt-2 text-[10px] px-2 py-1 bg-slate-900 rounded-full border border-slate-700 text-sky-300">
          {data.tier.toUpperCase()}
        </div>
      </div>
      <Handle type="source" position={Position.Bottom} className="w-3 h-3 bg-sky-400" />
    </div>
  );
};

const nodeTypes = { agent: AgentNode };

/**
 * Empreinte stable d'un graphe : ne retient que ce qui a un sens métier
 * (topologie, agents, tiers, positions arrondies). Les métadonnées de rendu de
 * React Flow — dimensions mesurées, sélection, survol — changent toutes seules
 * et ne doivent pas déclencher de sauvegarde.
 */
function graphSignature(
  nodes: ReadonlyArray<{ id: string; position?: { x: number; y: number }; data?: Record<string, unknown> }>,
  edges: ReadonlyArray<{ id: string; source?: string; target?: string; label?: unknown }>,
): string {
  const n = nodes
    .map((x) => [
      x.id,
      Math.round(x.position?.x ?? 0),
      Math.round(x.position?.y ?? 0),
      x.data?.agent ?? '',
      x.data?.tier ?? '',
      x.data?.label ?? '',
    ].join('|'))
    .sort()
    .join(';');
  const e = edges
    .map((x) => [x.id, x.source ?? '', x.target ?? '', String(x.label ?? '')].join('|'))
    .sort()
    .join(';');
  return `${n}##${e}`;
}

// --- Main Component ---
const WorkflowBuilderContent = () => {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [mode, setMode] = useState<'cascade' | 'dag'>('cascade');
  const [selectedNode, setSelectedNode] = useState<Node | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<Edge | null>(null);
  const [isImportModalOpen, setIsImportModalOpen] = useState(false);
  const [importJson, setImportJson] = useState('');
  
  const reactFlowWrapper = useRef<HTMLDivElement>(null);

  // Fetch initial data
  const { data: initialData, isLoading } = useQuery({
    queryKey: ['workflow'],
    queryFn: fetchWorkflow,
    refetchOnWindowFocus: false
  });

  /**
   * Empreinte du graphe tel qu'il a été chargé depuis le serveur. Sert de témoin
   * à l'auto-sauvegarde : sans elle, le simple fait d'ouvrir l'onglet réécrivait
   * `agents_workflows.json` deux secondes plus tard, sans aucune action de
   * l'utilisateur (constaté en direct : le fichier changeait à chaque visite).
   */
  const loadedSignature = useRef<string | null>(null);

  useEffect(() => {
    if (initialData) {
      setNodes(initialData.nodes as Node[]);
      // Cast to Edge[] pour compatibilité ReactFlow (label ReactNode vs string)
      setEdges(initialData.edges as unknown as Edge[]);
      loadedSignature.current = graphSignature(initialData.nodes, initialData.edges);
    }
  }, [initialData, setNodes, setEdges]);

  // Auto-save mutation
  const saveMutation = useMutation({
    mutationFn: () => saveWorkflow(nodes as Parameters<typeof saveWorkflow>[0], edges as Parameters<typeof saveWorkflow>[1]),
  });

  // Auto-sauvegarde différée — uniquement si le graphe diffère RÉELLEMENT de
  // celui qui a été chargé. Ouvrir l'onglet et repartir ne touche plus au fichier.
  useEffect(() => {
    if (loadedSignature.current === null) return; // chargement pas encore appliqué
    const current = graphSignature(nodes, edges);
    if (current === loadedSignature.current) return; // rien n'a changé
    const timer = setTimeout(() => {
      saveMutation.mutate();
      loadedSignature.current = current;
    }, 2000);
    return () => clearTimeout(timer);
  }, [nodes, edges]); // eslint-disable-line react-hooks/exhaustive-deps

  const onConnect = useCallback(
    (params: Connection) => setEdges((eds) => addEdge({ ...params, animated: true, style: { stroke: '#38bdf8', strokeWidth: 2 } }, eds)),
    [setEdges]
  );

  const onDragStart = (event: React.DragEvent, nodeType: string, agentName: string) => {
    event.dataTransfer.setData('application/reactflow', nodeType);
    event.dataTransfer.setData('agentName', agentName);
    event.dataTransfer.effectAllowed = 'move';
  };

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();
      const type = event.dataTransfer.getData('application/reactflow');
      const agentName = event.dataTransfer.getData('agentName');
      if (!type) return;

      const position = { x: event.clientX - 300, y: event.clientY - 100 }; // Rough offset
      const newNode: Node = {
        id: `node_${Date.now()}`,
        type,
        position,
        data: { label: `Nouvel ${agentName}`, agent: agentName, model: 'auto', tier: 'moyen' },
      };
      setNodes((nds) => nds.concat(newNode));
    },
    [setNodes]
  );

  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }, []);

  const handleExport = () => {
    const data = JSON.stringify({ nodes, edges }, null, 2);
    const blob = new Blob([data], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'workflow_export.json';
    a.click();
  };

  const handleImport = () => {
    try {
      const parsed = JSON.parse(importJson);
      if (parsed.nodes && parsed.edges) {
        setNodes(parsed.nodes);
        setEdges(parsed.edges);
        setIsImportModalOpen(false);
      }
    } catch (e) {
      alert("JSON invalide");
    }
  };

  if (isLoading) return <div className="p-8 text-slate-400">Chargement du workflow...</div>;

  return (
    <div className="flex flex-col h-full bg-slate-950 text-slate-200">
      {/* Header */}
      <header className="flex items-center justify-between px-6 py-4 border-b border-slate-800 bg-slate-900">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-2">
            <Settings2 className="w-6 h-6 text-sky-400" />
            Workflow Builder
          </h1>
          <p className="text-sm text-slate-400 mt-1">Éditeur visuel d'orchestration multi-agents</p>
        </div>
        <div className="flex items-center gap-4">
          <select 
            value={mode} 
            onChange={(e) => setMode(e.target.value as any)}
            className="bg-slate-800 border border-slate-700 text-sm rounded-lg px-3 py-2 focus:ring-2 focus:ring-sky-500 outline-none"
          >
            <option value="cascade">Séquentiel (Cascade)</option>
            <option value="dag">Parallèle (DAG)</option>
          </select>
          <button onClick={() => setIsImportModalOpen(true)} className="btn-secondary flex items-center gap-2 px-3 py-2 text-sm bg-slate-800 hover:bg-slate-700 rounded-lg border border-slate-700">
            <Upload className="w-4 h-4" /> Importer
          </button>
          <button onClick={handleExport} className="btn-secondary flex items-center gap-2 px-3 py-2 text-sm bg-slate-800 hover:bg-slate-700 rounded-lg border border-slate-700">
            <Download className="w-4 h-4" /> Exporter
          </button>
          <button onClick={() => { setNodes([]); setEdges([]); }} className="btn-secondary flex items-center gap-2 px-3 py-2 text-sm bg-red-900/30 text-red-400 hover:bg-red-900/50 rounded-lg border border-red-900/50">
            <RotateCcw className="w-4 h-4" /> Réinitialiser
          </button>
          <button onClick={() => saveMutation.mutate()} className="btn-primary flex items-center gap-2 px-4 py-2 text-sm bg-sky-600 hover:bg-sky-500 text-white rounded-lg font-medium transition-colors">
            <Save className="w-4 h-4" /> {saveMutation.isPending ? 'Sauvegarde...' : 'Sauvegarder'}
          </button>
        </div>
      </header>

      {/* Main Workspace */}
      <div className="flex flex-1 overflow-hidden">
        {/* React Flow Canvas */}
        <div className="flex-1 relative" ref={reactFlowWrapper}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onDrop={onDrop}
            onDragOver={onDragOver}
            onNodeClick={(_, node) => { setSelectedNode(node); setSelectedEdge(null); }}
            onEdgeClick={(_, edge) => { setSelectedEdge(edge); setSelectedNode(null); }}
            onPaneClick={() => { setSelectedNode(null); setSelectedEdge(null); }}
            nodeTypes={nodeTypes}
            fitView
            className="bg-slate-950"
          >
            <Background color="#334155" gap={16} size={1} />
            <Controls className="bg-slate-800 border-slate-700 fill-slate-200" />
            <MiniMap nodeColor="#0ea5e9" maskColor="#0f172a" className="bg-slate-900 border border-slate-800" />
            
            <Panel position="top-left" className="bg-slate-900/80 p-2 rounded-lg border border-slate-800 text-xs text-slate-400 backdrop-blur-sm">
              Glissez-déposez des agents depuis le panneau de droite.
            </Panel>
          </ReactFlow>
        </div>

        {/* Right Sidebar */}
        <aside className="w-80 bg-slate-900 border-l border-slate-800 flex flex-col overflow-y-auto">
          <div className="p-4 border-b border-slate-800">
            <h3 className="font-semibold text-slate-200 mb-3">Agents disponibles</h3>
            <div className="space-y-2">
              {['planner', 'executor', 'reviewer', 'ha_agent', 'antigravity'].map(agent => (
                <div
                  key={agent}
                  className="p-3 bg-slate-800 border border-slate-700 rounded-lg cursor-grab hover:border-sky-500 transition-colors text-sm font-medium"
                  onDragStart={(e) => onDragStart(e, 'agent', agent)}
                  draggable
                >
                  🤖 {agent}
                </div>
              ))}
            </div>
          </div>

          <div className="p-4 flex-1">
            {selectedNode ? (
              <div className="space-y-4">
                <h3 className="font-semibold text-sky-400 border-b border-slate-800 pb-2">Propriétés du Nœud</h3>
                <div>
                  <label className="block text-xs text-slate-400 mb-1">Label</label>
                  <input 
                    type="text" 
                    value={selectedNode.data.label as string}
                    onChange={(e) => setNodes(nds => nds.map(n => n.id === selectedNode.id ? { ...n, data: { ...n.data, label: e.target.value } } : n))}
                    className="w-full bg-slate-950 border border-slate-700 rounded p-2 text-sm focus:border-sky-500 outline-none"
                  />
                </div>
                <div>
                  <label className="block text-xs text-slate-400 mb-1">Modèle (LLM)</label>
                  <select 
                    value={selectedNode.data.model as string}
                    onChange={(e) => setNodes(nds => nds.map(n => n.id === selectedNode.id ? { ...n, data: { ...n.data, model: e.target.value } } : n))}
                    className="w-full bg-slate-950 border border-slate-700 rounded p-2 text-sm focus:border-sky-500 outline-none"
                  >
                    <option value="auto">Auto (Routeur)</option>
                    <option value="deepseek-reasoner">DeepSeek Reasoner</option>
                    <option value="claude-sonnet-4-6">Claude 3.5 Sonnet</option>
                    <option value="gemini-3.5-flash">Gemini 3.5 Flash</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-slate-400 mb-1">Tier budgétaire</label>
                  <select 
                    value={selectedNode.data.tier as string}
                    onChange={(e) => setNodes(nds => nds.map(n => n.id === selectedNode.id ? { ...n, data: { ...n.data, tier: e.target.value } } : n))}
                    className="w-full bg-slate-950 border border-slate-700 rounded p-2 text-sm focus:border-sky-500 outline-none"
                  >
                    <option value="leger">Léger (Gratuit/Local)</option>
                    <option value="moyen">Moyen (Équilibré)</option>
                    <option value="fort">Fort (Raisonnement lourd)</option>
                  </select>
                </div>
              </div>
            ) : selectedEdge ? (
              <div className="space-y-4">
                <h3 className="font-semibold text-emerald-400 border-b border-slate-800 pb-2">Propriétés de la Transition</h3>
                <div>
                  <label className="block text-xs text-slate-400 mb-1">Condition (Label)</label>
                  <input 
                    type="text" 
                    value={selectedEdge.label as string || ''}
                    onChange={(e) => setEdges(eds => eds.map(edge => edge.id === selectedEdge.id ? { ...edge, label: e.target.value } : edge))}
                    className="w-full bg-slate-950 border border-slate-700 rounded p-2 text-sm focus:border-emerald-500 outline-none"
                    placeholder="Ex: Si succès..."
                  />
                </div>
              </div>
            ) : (
              <div className="text-slate-500 text-sm text-center mt-10">
                Sélectionnez un nœud ou une arête pour éditer ses propriétés.
              </div>
            )}
          </div>
        </aside>
      </div>

      {/* Import Modal */}
      {isImportModalOpen && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50">
          <div className="bg-slate-900 border border-slate-700 rounded-xl p-6 w-full max-w-2xl shadow-2xl">
            <h2 className="text-xl font-bold mb-4">Importer un Workflow JSON</h2>
            <textarea 
              className="w-full h-64 bg-slate-950 border border-slate-700 rounded-lg p-4 font-mono text-sm text-sky-300 focus:border-sky-500 outline-none"
              placeholder='{"nodes": [...], "edges": [...]}'
              value={importJson}
              onChange={(e) => setImportJson(e.target.value)}
            />
            <div className="flex justify-end gap-3 mt-4">
              <button onClick={() => setIsImportModalOpen(false)} className="px-4 py-2 rounded-lg bg-slate-800 hover:bg-slate-700">Annuler</button>
              <button onClick={handleImport} className="px-4 py-2 rounded-lg bg-sky-600 hover:bg-sky-500 text-white font-medium">Importer</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default function WorkflowBuilder() {
  return (
    <ReactFlowProvider>
      <WorkflowBuilderContent />
    </ReactFlowProvider>
  );
}
