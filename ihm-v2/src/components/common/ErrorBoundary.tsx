import { Component, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

/**
 * Filet de sécurité : sans ça, une exception de rendu dans une seule vue (ex: champ
 * API manquant) démonte tout l'arbre React et laisse l'app blanche/inutilisable,
 * y compris pour naviguer vers un autre onglet qui fonctionne.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: { componentStack: string }): void {
    console.error("[ErrorBoundary] Vue plantée :", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="m-6 rounded-xl border border-red-900/50 bg-red-950/30 p-6 text-red-300">
          <h2 className="mb-2 text-lg font-semibold">Cette vue a rencontré une erreur</h2>
          <p className="mb-4 text-sm text-red-400/80">{this.state.error.message}</p>
          <button
            onClick={() => this.setState({ error: null })}
            className="rounded-lg border border-red-800 px-4 py-2 text-sm font-medium hover:bg-red-900/40"
          >
            Réessayer
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
