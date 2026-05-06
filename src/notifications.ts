import { useCallback } from 'react';
import { useTasks } from './tasks';

function normalizeNoticeMessage(message: unknown) {
  if (message instanceof Error) {
    return message.message;
  }
  return String(message || '');
}

export function useNotifier() {
  const { notify } = useTasks();

  const notifyError = useCallback(
    (message: unknown) => {
      notify({ kind: 'error', message: normalizeNoticeMessage(message) });
    },
    [notify],
  );

  const notifySuccess = useCallback(
    (message: string) => {
      notify({ kind: 'success', message });
    },
    [notify],
  );

  const notifyInfo = useCallback(
    (message: string) => {
      notify({ kind: 'info', message });
    },
    [notify],
  );

  return { notifyError, notifySuccess, notifyInfo };
}
