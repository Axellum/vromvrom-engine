/** Enregistrement unique des composants Chart.js utilisés par l'IHM. */
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  Tooltip,
  Legend,
  Filler,
} from "chart.js";

let registered = false;

export function ensureChartsRegistered(): void {
  if (registered) return;
  ChartJS.register(
    CategoryScale,
    LinearScale,
    PointElement,
    LineElement,
    BarElement,
    Tooltip,
    Legend,
    Filler,
  );
  ChartJS.defaults.color = "#94a3b8";
  ChartJS.defaults.borderColor = "rgba(148, 163, 184, 0.12)";
  registered = true;
}
