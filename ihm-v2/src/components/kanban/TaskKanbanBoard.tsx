/**
 * Composant TaskKanbanBoard
 * Affiche les tâches sous forme de tableau Kanban.
 * 
 * @author Agent Executor
 * @date 2026-08-17
 */

import React from 'react';

// Interface pour une carte de tâche
interface TaskCardProps {
  id: string;
  status: string;
  title: string;
}

// Composant de carte individuelle
export const TaskCard: React.FC<TaskCardProps> = ({ id, status, title }) => {
  return (
    <div className="task-card">
      <h3>{title}</h3>
      <p>ID: {id}</p>
      <span className="badge">{status}</span>
    </div>
  );
};

// Composant principal du tableau Kanban
export const TaskKanbanBoard: React.FC = () => {
  return (
    <div className="kanban-board">
      <h2>Tableau des tâches</h2>
      {/* Structure des colonnes Kanban */}
      <div className="kanban-columns">
        <div className="column">
          <h4>Pending</h4>
        </div>
        <div className="column">
          <h4>Running</h4>
        </div>
        <div className="column">
          <h4>Success</h4>
        </div>
        <div className="column">
          <h4>Error</h4>
        </div>
      </div>
    </div>
  );
};
