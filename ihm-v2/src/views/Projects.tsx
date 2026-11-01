/**
 * Vue Projets — chaque livrable du moteur va dans LE git du projet visé.
 *
 * Sans ce registre, une tâche Tab5 écrivait dans le clone moteur (ou à côté,
 * hors de tout commit). Ici on constate le mapping réel : dossier local,
 * remote Git, clone agent vs copie humaine.
 */
import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, AlertTriangle, XCircle, RefreshCw } from "lucide-react";
import { fetchProjects, type EngineProject, type ProjectStatus } from "../api/projects";
import {
  PageHeader, Card, Explain, Button, Pill,
  LoadingState, ErrorState,
} from "../components/ui/primitives";

const ICONS: Record<ProjectStatus, React.ElementType> = {
  ok: CheckCircle2,
  warn: AlertTriangle,
  error: XCircle,
};

const COLORS: Record<ProjectStatus, string> = {
  ok: "text-emerald-400",
  warn: "text-amber-400",
  error: "text-red-400",
};

function ProjectCard({ projet }: { projet: EngineProject }) {
  const Icon = ICONS[projet.status];
  return (
    <Card
      title={projet.label}
      subtitle={`id ${projet.id} · remote ${projet.git_remote}`}
      source="GET /api/projects"
    >
      <div className="space-y-3">
        <div className="flex items-center gap-2">
          <Icon className={`h-4 w-4 ${COLORS[projet.status]}`} />
          <span className="text-sm text-slate-200">{projet.detail}</span>
        </div>
        <dl className="grid grid-cols-1 gap-2 text-xs sm:grid-cols-2">
          <div>
            <dt className="text-slate-500">Dossier local</dt>
            <dd className="break-all font-mono text-slate-300">{projet.local_path ?? "—"}</dd>
          </div>
          <div>
            <dt className="text-slate-500">Dépôt de travail agent</dt>
            <dd className="break-all font-mono text-slate-300">{projet.work_path ?? "—"}</dd>
          </div>
          <div>
            <dt className="text-slate-500">Branche courante</dt>
            <dd className="font-mono text-slate-300">{projet.git_branch ?? "—"}</dd>
          </div>
          <div>
            <dt className="text-slate-500">Politique</dt>
            <dd>
              <Pill tone={projet.politique === "clone" ? "info" : "warn"}>
                {projet.politique === "clone" ? "clone dédié (reset OK)" : "copie humaine (stash, pas de wipe)"}
              </Pill>
            </dd>
          </div>
        </dl>
        <p className="text-xs text-slate-500">
          Zones board : {projet.zone_prefixes.length ? projet.zone_prefixes.join(", ") : "repli par défaut"}
        </p>
      </div>
    </Card>
  );
}

export function Projects() {
  const q = useQuery({ queryKey: ["projects"], queryFn: fetchProjects });

  if (q.isLoading) return <LoadingState label="Lecture du registre des projets…" />;
  if (q.isError) {
    return (
      <div className="mx-auto max-w-4xl">
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      </div>
    );
  }

  const data = q.data;
  if (!data) return null;

  return (
    <div className="mx-auto max-w-5xl pb-16">
      <PageHeader
        title="Projets"
        description={
          <>
            Un projet = un git + un dossier local. Les livrables Tab5 vont dans{" "}
            <code>Axellum/M5-Tab5-ESPHome-LVGL</code>, pas dans le clone du moteur.
          </>
        }
      />
      <div className="mb-6 space-y-3">
        <Explain>
          Racine de lecture de l&apos;écosystème :{" "}
          <code className="break-all">{data.workspace_root}</code>
          {data.overlay ? (
            <> · overlay <code className="break-all">{data.overlay}</code></>
          ) : (
            <> · pas d&apos;overlay <code>projects.json</code> (détection automatique)</>
          )}
        </Explain>
        <Button variant="secondary" onClick={() => q.refetch()}>
          <RefreshCw className="h-4 w-4" />
          Resonder
        </Button>
      </div>
      <div className="space-y-4">
        {data.projects.map((p) => (
          <ProjectCard key={p.id} projet={p} />
        ))}
      </div>
    </div>
  );
}

export default Projects;
