import { apiFetch } from "./client";

export type ProjectStatus = "ok" | "warn" | "error";

export interface EngineProject {
  id: string;
  label: string;
  git_remote: string;
  default_branch: string;
  politique: "clone" | "humain";
  zone_prefixes: string[];
  local_path: string | null;
  work_path: string | null;
  est_clone_agent: boolean;
  git_origin: string | null;
  git_branch: string | null;
  git_ok: boolean;
  status: ProjectStatus;
  detail: string;
}

export interface ProjectsSnapshot {
  workspace_root: string;
  overlay: string | null;
  projects: EngineProject[];
  ok: boolean;
}

export async function fetchProjects(): Promise<ProjectsSnapshot> {
  return apiFetch<ProjectsSnapshot>("/api/projects");
}
