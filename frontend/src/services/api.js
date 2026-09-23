/**
 * TaskLane Centralized API Client Abstraction
 * Supports both real backend calls and mocked local execution.
 */

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL !== undefined
    ? import.meta.env.VITE_API_BASE_URL
    : import.meta.env.DEV
      ? 'http://localhost:8000'
      : '';
let useMock = false;

// Initial mock dataset for development and demonstration
const DEFAULT_TASKS = [
  {
    id: 1,
    title: 'Design OpenAPI contract',
    description: 'Draft the openapi.yaml specification matching _docs/specs.md',
    status: 'in_progress',
    priority: 'high',
    created_at: '2026-09-14T10:00:00Z',
    updated_at: '2026-09-14T10:00:00Z'
  },
  {
    id: 2,
    title: 'Implement Kanban UI prototype',
    description: 'Build responsive 5-column board with mock backend integration',
    status: 'review',
    priority: 'urgent',
    created_at: '2026-09-14T11:00:00Z',
    updated_at: '2026-09-14T11:00:00Z'
  },
  {
    id: 3,
    title: 'Draft initial product backlog',
    description: 'Break down specifications into clear acceptance criteria',
    status: 'done',
    priority: 'medium',
    created_at: '2026-09-14T09:00:00Z',
    updated_at: '2026-09-14T09:30:00Z'
  },
  {
    id: 4,
    title: 'Configure FastAPI backend project',
    description: 'Set up uv, FastAPI dependencies, and SQLAlchemy models',
    status: 'todo',
    priority: 'high',
    created_at: '2026-09-14T12:00:00Z',
    updated_at: '2026-09-14T12:00:00Z'
  },
  {
    id: 5,
    title: 'Write automated pytest suite',
    description: 'Cover creation, transitions, validation errors, and 404s',
    status: 'backlog',
    priority: 'medium',
    created_at: '2026-09-14T12:30:00Z',
    updated_at: '2026-09-14T12:30:00Z'
  }
];

const STORAGE_KEY = 'tasklane_mock_tasks';

function loadMockTasks() {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (!stored) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(DEFAULT_TASKS));
    return [...DEFAULT_TASKS];
  }
  try {
    return JSON.parse(stored);
  } catch {
    return [...DEFAULT_TASKS];
  }
}

function saveMockTasks(tasks) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(tasks));
}

export const apiClient = {
  isMock: () => useMock,
  setMock: (value) => {
    useMock = Boolean(value);
  },

  async getTasks({ status, priority, search } = {}) {
    if (useMock) {
      let tasks = loadMockTasks();

      if (status) {
        tasks = tasks.filter((t) => t.status === status);
      }
      if (priority) {
        tasks = tasks.filter((t) => t.priority === priority);
      }
      if (search && search.trim() !== '') {
        const query = search.trim().toLowerCase();
        tasks = tasks.filter(
          (t) =>
            t.title.toLowerCase().includes(query) ||
            (t.description && t.description.toLowerCase().includes(query))
        );
      }

      // Sort newest first by created_at DESC, id DESC
      tasks.sort((a, b) => {
        const timeDiff = new Date(b.created_at) - new Date(a.created_at);
        if (timeDiff !== 0) return timeDiff;
        return b.id - a.id;
      });

      return [...tasks];
    }

    const params = new URLSearchParams();
    if (status) params.append('status', status);
    if (priority) params.append('priority', priority);
    if (search) params.append('search', search);

    const queryString = params.toString() ? `?${params.toString()}` : '';
    const res = await fetch(`${API_BASE_URL}/api/tasks${queryString}`);
    if (!res.ok) {
      throw new Error(`Failed to fetch tasks: ${res.statusText}`);
    }
    return res.json();
  },

  async createTask({ title, description = '', priority = 'medium' }) {
    const trimmedTitle = (title || '').trim();
    if (!trimmedTitle) {
      throw new Error('Title cannot be empty');
    }

    if (useMock) {
      const tasks = loadMockTasks();
      const nextId = tasks.length > 0 ? Math.max(...tasks.map((t) => t.id)) + 1 : 1;
      const now = new Date().toISOString();

      const newTask = {
        id: nextId,
        title: trimmedTitle,
        description: (description || '').trim(),
        status: 'backlog', // Strictly starts in backlog
        priority: priority || 'medium',
        created_at: now,
        updated_at: now
      };

      tasks.unshift(newTask);
      saveMockTasks(tasks);
      return { ...newTask };
    }

    const res = await fetch(`${API_BASE_URL}/api/tasks`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: trimmedTitle, description, priority })
    });

    if (!res.ok) {
      const errorData = await res.json().catch(() => ({}));
      throw new Error(errorData.detail?.[0]?.msg || errorData.detail || 'Failed to create task');
    }
    return res.json();
  },

  async updateTask(id, patchData) {
    if (useMock) {
      const tasks = loadMockTasks();
      const index = tasks.findIndex((t) => t.id === Number(id));
      if (index === -1) {
        throw new Error('Task not found');
      }

      const updated = {
        ...tasks[index],
        ...patchData,
        updated_at: new Date().toISOString()
      };
      tasks[index] = updated;
      saveMockTasks(tasks);
      return { ...updated };
    }

    const res = await fetch(`${API_BASE_URL}/api/tasks/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patchData)
    });

    if (!res.ok) {
      throw new Error('Failed to update task');
    }
    return res.json();
  },

  async deleteTask(id) {
    if (useMock) {
      const tasks = loadMockTasks();
      const filtered = tasks.filter((t) => t.id !== Number(id));
      if (filtered.length === tasks.length) {
        throw new Error('Task not found');
      }
      saveMockTasks(filtered);
      return true;
    }

    const res = await fetch(`${API_BASE_URL}/api/tasks/${id}`, {
      method: 'DELETE'
    });

    if (!res.ok) {
      throw new Error('Failed to delete task');
    }
    return true;
  },

  resetMockData() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(DEFAULT_TASKS));
  }
};
