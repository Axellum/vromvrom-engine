import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
      // `networkMode: "always"` — le moteur est sur le LAN (souvent localhost) :
      // l'état « en ligne » du navigateur ne dit rien de sa joignabilité. En mode
      // "online" (défaut), React Query SUSPEND les requêtes dès qu'il se croit
      // hors ligne : la requête n'est alors ni en cours, ni en erreur, et les vues
      // affichaient leur état « aucune donnée » — un résultat vide qui n'en était
      // pas un. Observé en conditions réelles sur /api/ha/entities.
      networkMode: "always",
    },
    mutations: { networkMode: "always" },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
