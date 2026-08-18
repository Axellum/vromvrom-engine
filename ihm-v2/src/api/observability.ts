/** Endpoint de télémétrie pour la vue Observabilité. */
import { apiFetch } from "./client";
import type { Telemetry, TelemetryPeriod } from "../types/observability";

export function fetchTelemetry(period: TelemetryPeriod): Promise<Telemetry> {
  return apiFetch<Telemetry>(`/api/metrics/telemetry?period=${encodeURIComponent(period)}`);
}
