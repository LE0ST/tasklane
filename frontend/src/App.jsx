import React, { useState, useEffect, useCallback } from 'react';
import { apiClient } from './services/api';
import './App.css';

const COLUMNS = [
  { id: 'backlog', title: 'Backlog' },
  { id: 'todo', title: 'To Do' },
  { id: 'in_progress', title: 'In Progress' },
  { id: 'review', title: 'Review' },
  { id: 'done', title: 'Done' }
];

const STATUS_ORDER = ['backlog', 'todo', 'in_progress', 'review', 'done'];

export default function App() {
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [priorityFilter, setPriorityFilter] = useState('');
  const [isModalOpen, setIsModalOpen] = useState(false);
  
  // Form State
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [priority, setPriority] = useState('medium');
  const [formError, setFormError] = useState('');

  const loadTasks = useCallback(async () => {
    setLoading(true);
    try {
      const data = await apiClient.getTasks({
        search,
        priority: priorityFilter || undefined
      });
      setTasks(data);
    } catch (err) {
      console.error('Failed to load tasks:', err);
    } finally {
      setLoading(false);
    }
  }, [search, priorityFilter]);

  useEffect(() => {
    loadTasks();
  }, [loadTasks]);

  const handleCreateTask = async (e) => {
    e.preventDefault();
    if (!title.trim()) {
      setFormError('Title is required and cannot be empty.');
      return;
    }
    setFormError('');

    try {
      await apiClient.createTask({
        title: title.trim(),
        description: description.trim(),
        priority
      });
      setTitle('');
      setDescription('');
      setPriority('medium');
      setIsModalOpen(false);
      await loadTasks();
    } catch (err) {
      setFormError(err.message || 'Error creating task');
    }
  };

  const handleMove = async (task, direction) => {
    const currentIndex = STATUS_ORDER.indexOf(task.status);
    if (currentIndex === -1) return;

    let nextIndex = currentIndex;
    if (direction === 'next' && currentIndex < STATUS_ORDER.length - 1) {
      nextIndex = currentIndex + 1;
    } else if (direction === 'prev' && currentIndex > 0) {
      nextIndex = currentIndex - 1;
    }

    if (nextIndex !== currentIndex) {
      const newStatus = STATUS_ORDER[nextIndex];
      try {
        await apiClient.updateTask(task.id, { status: newStatus });
        await loadTasks();
      } catch (err) {
        console.error('Failed to update task status:', err);
      }
    }
  };

  const handleDelete = async (id) => {
    if (!window.confirm('Are you sure you want to delete this task?')) return;
    try {
      await apiClient.deleteTask(id);
      await loadTasks();
    } catch (err) {
      console.error('Failed to delete task:', err);
    }
  };

  return (
    <div className="app-container">
      {/* Header */}
      <header className="app-header">
        <div className="brand-section">
          <h1>TaskLane</h1>
          <p>Mini Kanban Board &middot; Mock Backend Mode</p>
        </div>

        <div className="header-controls">
          <input
            type="text"
            className="search-input"
            placeholder="Search tasks..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />

          <select
            className="priority-select"
            value={priorityFilter}
            onChange={(e) => setPriorityFilter(e.target.value)}
          >
            <option value="">All Priorities</option>
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
            <option value="urgent">Urgent</option>
          </select>

          <button className="btn-primary" onClick={() => setIsModalOpen(true)}>
            + New Task
          </button>
        </div>
      </header>

      {/* Board */}
      <main className="kanban-board">
        {COLUMNS.map((column) => {
          const columnTasks = tasks.filter((t) => t.status === column.id);
          const colIndex = STATUS_ORDER.indexOf(column.id);

          return (
            <div key={column.id} className="kanban-column">
              <div className="column-header">
                <span className="column-title">{column.title}</span>
                <span className="task-count">{columnTasks.length}</span>
              </div>

              <div className="column-tasks">
                {columnTasks.length === 0 ? (
                  <div className="empty-column-msg">No tasks</div>
                ) : (
                  columnTasks.map((task) => (
                    <div key={task.id} className="task-card">
                      <div className="task-card-header">
                        <span className="task-title">{task.title}</span>
                        <button
                          className="btn-delete"
                          title="Delete task"
                          onClick={() => handleDelete(task.id)}
                        >
                          &times;
                        </button>
                      </div>

                      {task.description && (
                        <p className="task-description">{task.description}</p>
                      )}

                      <div className="task-card-footer">
                        <span className={`priority-badge priority-${task.priority}`}>
                          {task.priority}
                        </span>

                        <div className="task-nav-buttons">
                          <button
                            className="btn-nav"
                            title="Move left"
                            disabled={colIndex === 0}
                            onClick={() => handleMove(task, 'prev')}
                          >
                            &larr;
                          </button>
                          <button
                            className="btn-nav"
                            title="Move right"
                            disabled={colIndex === STATUS_ORDER.length - 1}
                            onClick={() => handleMove(task, 'next')}
                          >
                            &rarr;
                          </button>
                        </div>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          );
        })}
      </main>

      {/* New Task Modal */}
      {isModalOpen && (
        <div className="modal-overlay" onClick={() => setIsModalOpen(false)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2>Create New Task</h2>
              <button className="btn-delete" onClick={() => setIsModalOpen(false)}>
                &times;
              </button>
            </div>

            <form onSubmit={handleCreateTask}>
              {formError && <p className="error-msg">{formError}</p>}

              <div className="form-group">
                <label>Title *</label>
                <input
                  type="text"
                  className="form-input"
                  placeholder="Task title (required)"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  maxLength={120}
                  autoFocus
                />
              </div>

              <div className="form-group">
                <label>Description</label>
                <textarea
                  className="form-textarea"
                  placeholder="Optional details or context"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                />
              </div>

              <div className="form-group">
                <label>Priority</label>
                <select
                  className="form-select"
                  value={priority}
                  onChange={(e) => setPriority(e.target.value)}
                >
                  <option value="low">Low</option>
                  <option value="medium">Medium</option>
                  <option value="high">High</option>
                  <option value="urgent">Urgent</option>
                </select>
              </div>

              <div className="modal-footer">
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => setIsModalOpen(false)}
                >
                  Cancel
                </button>
                <button type="submit" className="btn-primary">
                  Create in Backlog
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
