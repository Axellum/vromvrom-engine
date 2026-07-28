import { apiFetch } from './client';

export interface WorkflowNodeData {
  label: string;
  agent: string;
  model: string;
  tier: string;
  [key: string]: unknown;
}

export interface WorkflowNode {
  id: string;
  type: string;
  position: { x: number; y: number };
  data: WorkflowNodeData;
}

export interface WorkflowEdge {
  id: string;
  source: string;
  target: string;
  label?: string;
  animated?: boolean;
}

export interface WorkflowGraph {
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
}

/** Format réel de `agents_workflows.json` (vérifié en direct via `GET /api/workflows`) :
 * nœuds à `x`/`y` plats + `agentName`/`label` au niveau racine (pas `position`/`data`),
 * arêtes `from`/`to` (pas `source`/`target`), clé `connections` (pas `edges`). Très
 * différent du format `WorkflowGraph` attendu par `@xyflow/react` — normalisé ici.
 */
interface RawWorkflowNode {
  id: string;
  type?: string;
  label?: string;
  x?: number;
  y?: number;
  tier?: string | null;
  agentName?: string | null;
  position?: { x: number; y: number };
  data?: WorkflowNodeData;
}
interface RawWorkflowEdge {
  id: string;
  from?: string;
  to?: string;
  fromPort?: string;
  source?: string;
  target?: string;
  label?: string;
  animated?: boolean;
}

function normalizeNode(n: RawWorkflowNode): WorkflowNode {
  return {
    id: n.id,
    type: 'agent', // seul type de nœud custom enregistré dans WorkflowBuilder (nodeTypes)
    position: n.position ?? { x: n.x ?? 0, y: n.y ?? 0 },
    data: n.data ?? {
      label: n.label ?? n.agentName ?? n.id,
      agent: n.agentName ?? '?',
      model: 'auto',
      tier: n.tier ?? 'moyen',
    },
  };
}

function normalizeEdge(e: RawWorkflowEdge): WorkflowEdge {
  return {
    id: e.id,
    source: e.source ?? e.from ?? '',
    target: e.target ?? e.to ?? '',
    label: e.label ?? e.fromPort,
  };
}

/**
 * Récupère le graphe de workflow actuel, normalisé depuis le format brut du backend.
 *
 * Aucun repli sur des données factices : `GET /api/workflows` existe bel et bien
 * (`api/routes/workflows.py`, `@router.get("")` sous le préfixe `/api/workflows`)
 * et renvoie `{nodes: [], connections: [], metadata: {}}` quand rien n'est encore
 * sauvegardé. Une erreur ici est une vraie erreur : elle doit remonter à la vue.
 */
export async function fetchWorkflow(): Promise<WorkflowGraph> {
  const raw = await apiFetch<{
    nodes?: RawWorkflowNode[];
    edges?: RawWorkflowEdge[];
    connections?: RawWorkflowEdge[];
  }>('/api/workflows');
  return {
    nodes: (raw.nodes ?? []).map(normalizeNode),
    edges: (raw.edges ?? raw.connections ?? []).map(normalizeEdge),
  };
}

/**
 * Sauvegarde le graphe complet dans `agents_workflows.json` (POST — le backend
 * n'expose pas de PUT sur cette route). Le backend écrit le format qu'on lui
 * envoie ; on conserve donc la clé `connections` attendue par `WorkflowBridge`
 * en plus de `edges`, pour rester lisible par le moteur comme par l'IHM.
 */
export async function saveWorkflow(nodes: WorkflowNode[], edges: WorkflowEdge[]): Promise<void> {
  await apiFetch<void>('/api/workflows', {
    method: 'POST',
    body: JSON.stringify({
      nodes: nodes.map((n) => ({
        id: n.id,
        type: n.type,
        label: n.data.label,
        agentName: n.data.agent,
        tier: n.data.tier,
        x: n.position.x,
        y: n.position.y,
      })),
      connections: edges.map((e) => ({ id: e.id, from: e.source, to: e.target, label: e.label })),
      metadata: { saved_by: 'ihm-v2', saved_at: new Date().toISOString() },
    }),
  });
}

/** Applique le workflow sauvegardé au moteur (recharge le WorkflowBridge). */
export async function applyWorkflow(): Promise<{
  message: string;
  agents: string[];
  custom_agents: string[];
}> {
  return apiFetch('/api/workflows/apply', { method: 'POST' });
}

/** Liste les workflows nommés disponibles. */
export async function listWorkflows(): Promise<string[]> {
  const res = await apiFetch<{ workflows: string[] }>('/api/workflows/list');
  return res.workflows ?? [];
}
