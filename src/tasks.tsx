import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { getImageTask, HistoryItem, ImageTask, listImageTasks } from './api';
import { useAuth } from './auth';

export type TaskStatusToast = {
  type: 'task';
  id: string;
  taskId: string;
  status: 'succeeded' | 'failed';
  prompt: string;
  createdAt: number;
  error: string | null;
};

export type NoticeToast = {
  type: 'notice';
  id: string;
  kind: 'success' | 'error' | 'info';
  title?: string;
  message: string;
  createdAt: number;
};

export type TaskToast = TaskStatusToast | NoticeToast;

export type NotifyInput = {
  kind?: NoticeToast['kind'];
  title?: string;
  message: string;
};

type TaskCenterValue = {
  tasks: ImageTask[];
  activeCount: number;
  drawerOpen: boolean;
  taskHistoryItems: HistoryItem[];
  toasts: TaskToast[];
  openDrawer: () => void;
  closeDrawer: () => void;
  toggleDrawer: () => void;
  addTask: (task: ImageTask) => void;
  refreshTasks: () => Promise<void>;
  notify: (toast: NotifyInput) => void;
  dismissToast: (toastId: string) => void;
};

const TASK_FETCH_LIMIT = 20;
const TASK_POLL_INTERVAL_MS = 1500;

const TaskCenterContext = createContext<TaskCenterValue | null>(null);

function sortTasks(tasks: ImageTask[]) {
  return [...tasks].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
}

function mergeTasks(current: ImageTask[], incoming: ImageTask[]) {
  const merged = new Map<string, ImageTask>();
  for (const task of current) {
    merged.set(task.id, task);
  }
  for (const task of incoming) {
    merged.set(task.id, task);
  }
  return sortTasks(Array.from(merged.values())).slice(0, TASK_FETCH_LIMIT);
}

function mergeHistoryItems(items: HistoryItem[]) {
  const merged = new Map<string, HistoryItem>();
  for (const item of items) {
    merged.set(item.id, item);
  }
  return [...merged.values()].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
}

export function TaskCenterProvider({ children }: { children: ReactNode }) {
  const { viewer } = useAuth();
  const [tasks, setTasks] = useState<ImageTask[]>([]);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [toasts, setToasts] = useState<TaskToast[]>([]);
  const tasksRef = useRef<ImageTask[]>([]);

  const dismissToast = useCallback((toastId: string) => {
    setToasts((current) => current.filter((toast) => toast.id !== toastId));
  }, []);

  const notify = useCallback((toast: NotifyInput) => {
    const message = toast.message.trim();
    if (!message) {
      return;
    }
    setToasts((current) => [
      {
        type: 'notice',
        id: `notice:${Date.now()}:${Math.random().toString(36).slice(2)}`,
        kind: toast.kind || 'info',
        title: toast.title,
        message,
        createdAt: Date.now(),
      },
      ...current,
    ].slice(0, 6));
  }, []);

  const notifyCompletedTasks = useCallback((previous: ImageTask[], next: ImageTask[]) => {
    const previousById = new Map(previous.map((task) => [task.id, task]));
    const completed = next.filter((task) => {
      const previousTask = previousById.get(task.id);
      if (!previousTask) {
        return false;
      }
      const wasActive = previousTask.status === 'queued' || previousTask.status === 'running';
      const isTerminal = task.status === 'succeeded' || task.status === 'failed';
      return wasActive && isTerminal;
    });
    if (completed.length === 0) {
      return;
    }
    const newToasts = completed.map((task) => ({
      type: 'task' as const,
      id: `${task.id}:${task.status}:${task.updated_at}`,
      taskId: task.id,
      status: task.status,
      prompt: task.prompt,
      createdAt: Date.now(),
      error: task.error,
    }));
    setToasts((current) => [...newToasts, ...current].slice(0, 6));
  }, []);

  const refreshTasks = useCallback(async () => {
    if (!viewer?.owner_id) {
      tasksRef.current = [];
      setTasks([]);
      setToasts([]);
      return;
    }
    try {
      const response = await listImageTasks({ limit: TASK_FETCH_LIMIT });
      const nextTasks = sortTasks(response.items);
      tasksRef.current = nextTasks;
      setTasks(nextTasks);
    } catch {
      tasksRef.current = [];
      setTasks([]);
    }
  }, [viewer?.owner_id]);

  useEffect(() => {
    refreshTasks().catch(() => undefined);
  }, [refreshTasks]);

  const addTask = useCallback((task: ImageTask) => {
    const merged = mergeTasks(tasksRef.current, [task]);
    tasksRef.current = merged;
    setTasks(merged);
    if (task.status === 'succeeded' || task.status === 'failed') {
      setToasts((current) => [
        {
          type: 'task',
          id: `${task.id}:${task.status}:${task.updated_at}`,
          taskId: task.id,
          status: task.status,
          prompt: task.prompt,
          createdAt: Date.now(),
          error: task.error,
        },
        ...current,
      ].slice(0, 6));
    }
    setDrawerOpen(true);
  }, []);

  const activeTaskIds = useMemo(
    () =>
      tasks
        .filter((task) => task.status === 'queued' || task.status === 'running')
        .map((task) => task.id),
    [tasks],
  );
  const activeTaskKey = activeTaskIds.join(':');

  useEffect(() => {
    if (!viewer?.owner_id || activeTaskIds.length === 0) {
      return;
    }
    let cancelled = false;
    let timer = 0;
    let polling = false;

    const poll = async () => {
      if (cancelled || polling) {
        return;
      }
      polling = true;
      try {
        const updates = await Promise.all(activeTaskIds.map((taskId) => getImageTask(taskId).catch(() => null)));
        if (cancelled) {
          return;
        }
        const nextTasks = updates.filter((task): task is ImageTask => Boolean(task));
        if (nextTasks.length > 0) {
          const previous = tasksRef.current;
          const merged = mergeTasks(previous, nextTasks);
          tasksRef.current = merged;
          setTasks(merged);
          notifyCompletedTasks(previous, merged);
        }
      } finally {
        polling = false;
        if (!cancelled) {
          timer = window.setTimeout(poll, TASK_POLL_INTERVAL_MS);
        }
      }
    };

    timer = window.setTimeout(poll, 500);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [activeTaskKey, activeTaskIds, viewer?.owner_id]);

  const taskHistoryItems = useMemo(
    () => mergeHistoryItems(tasks.flatMap((task) => task.items || [])),
    [tasks],
  );

  useEffect(() => {
    if (toasts.length === 0) {
      return;
    }
    const timers = toasts.map((toast) =>
      window.setTimeout(() => {
        dismissToast(toast.id);
      }, 5000),
    );
    return () => {
      for (const timer of timers) {
        window.clearTimeout(timer);
      }
    };
  }, [dismissToast, toasts]);

  const value = useMemo<TaskCenterValue>(
    () => ({
      tasks,
      activeCount: activeTaskIds.length,
      drawerOpen,
      taskHistoryItems,
      toasts,
      openDrawer: () => setDrawerOpen(true),
      closeDrawer: () => setDrawerOpen(false),
      toggleDrawer: () => setDrawerOpen((current) => !current),
      addTask,
      refreshTasks,
      notify,
      dismissToast,
    }),
    [activeTaskIds.length, addTask, dismissToast, drawerOpen, notify, refreshTasks, taskHistoryItems, tasks, toasts],
  );

  return <TaskCenterContext.Provider value={value}>{children}</TaskCenterContext.Provider>;
}

export function useTasks() {
  const context = useContext(TaskCenterContext);
  if (!context) {
    throw new Error('useTasks must be used inside TaskCenterProvider');
  }
  return context;
}
