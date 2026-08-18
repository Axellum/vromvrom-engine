import { apiFetch } from "./client";

/** POST /api/prompt/engineer (#T192) — PromptEngineerAgent branché sur l'IHM. */
export async function engineerPrompt(
  prompt: string,
  context = "",
  tier?: string,
): Promise<{ optimized_prompt: string; provider_used: string }> {
  return apiFetch<{ optimized_prompt: string; provider_used: string }>("/api/prompt/engineer", {
    method: "POST",
    body: JSON.stringify({ prompt, context, ...(tier ? { tier } : {}) }),
  });
}
