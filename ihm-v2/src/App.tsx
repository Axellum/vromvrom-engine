import { useCallback, useEffect, useState } from "react";
import { getApiKey, registerAuthErrorHandler } from "./api/client";
import { useEngineStream } from "./hooks/useEngineStream";
import AppLayout from "./components/layout/AppLayout";
import { ApiKeyGate } from "./components/common/ApiKeyGate";
import { ErrorBoundary } from "./components/common/ErrorBoundary";

// Pilotage
import { Dashboard } from "./views/Dashboard";
import { Chat } from "./views/Chat";
import TaskRunner from "./views/TaskRunner";

// Domotique & vocal
import HomeAssistantView from "./views/HomeAssistant";

// Moteur & codage
import { AgentsManager } from "./views/AgentsManager";
import WorkflowBuilder from "./views/WorkflowBuilder";
import { LLMRegistry } from "./views/LLMRegistry";
import { PromptStudio } from "./views/PromptStudio";
import Benchmarks from "./views/Benchmarks";

// Autonomie
import Autonomy from "./views/Autonomy";

// Optimisation
import { Operations } from "./views/Operations";
import { Observability } from "./views/Observability";
import { APIAccounts } from "./views/APIAccounts";
import { Configuration } from "./views/Configuration";

// Comprendre
import Setup from "./views/Setup";
import Docs from "./views/Docs";

import { useUiStore } from "./state/uiStore";

export default function App() {
  const [needsKey, setNeedsKey] = useState(!getApiKey());
  const [authNonce, setAuthNonce] = useState(0);
  const view = useUiStore((s) => s.view);

  const handleAuthError = useCallback(() => setNeedsKey(true), []);
  useEffect(() => {
    registerAuthErrorHandler(handleAuthError);
    return () => registerAuthErrorHandler(null);
  }, [handleAuthError]);
  useEngineStream({ enabled: !needsKey });

  const renderView = () => {
    switch (view) {
      // Pilotage
      case "chat":           return <Chat key={authNonce} />;
      case "taskrunner":     return <TaskRunner key={authNonce} />;
      // Domotique & vocal
      case "homeassistant":  return <HomeAssistantView key={authNonce} />;
      // Moteur & codage
      case "agents":         return <AgentsManager key={authNonce} />;
      case "workflow":       return <WorkflowBuilder key={authNonce} />;
      case "llm":            return <LLMRegistry key={authNonce} />;
      case "prompt":         return <PromptStudio key={authNonce} />;
      case "benchmarks":     return <Benchmarks key={authNonce} />;
      // Autonomie
      case "autonomy":       return <Autonomy key={authNonce} />;
      // Optimisation
      case "operations":     return <Operations key={authNonce} />;
      case "observability":  return <Observability key={authNonce} />;
      case "accounts":       return <APIAccounts key={authNonce} />;
      case "config":         return <Configuration key={authNonce} />;
      // Comprendre
      case "setup":          return <Setup key={authNonce} />;
      case "docs":           return <Docs key={authNonce} />;
      default:               return <Dashboard key={authNonce} />;
    }
  };

  return (
    <>
      <ApiKeyGate
        needsKey={needsKey}
        onSubmit={() => {
          setNeedsKey(false);
          setAuthNonce((n) => n + 1);
        }}
      />
      <AppLayout>
        <div className="p-6">
          <ErrorBoundary key={view}>{renderView()}</ErrorBoundary>
        </div>
      </AppLayout>
    </>
  );
}
