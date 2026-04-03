import { create } from "zustand";
import { persist } from "zustand/middleware";
import { uuid } from "@/lib/utils";

export type NotificationType =
  | "pipeline_complete"
  | "pipeline_error"
  | "publish_success"
  | "publish_failure"
  | "review_needed"
  | "info";

export interface NotificationItem {
  id: string;
  type: NotificationType;
  title: string;
  body: string;
  read: boolean;
  created_at: string;
  entity_id?: string;
  entity_type?: string;
}

interface NotificationState {
  notifications: NotificationItem[];
  unreadCount: number;
  addNotification: (
    n: Omit<NotificationItem, "id" | "read" | "created_at">,
  ) => void;
  markRead: (id: string) => void;
  markAllRead: () => void;
  clearAll: () => void;
}

const MAX_NOTIFICATIONS = 200;

export const useNotificationStore = create<NotificationState>()(
  persist(
    (set) => ({
      notifications: [],
      unreadCount: 0,

      addNotification: (n) =>
        set((state) => {
          const item: NotificationItem = {
            ...n,
            id: uuid(),
            read: false,
            created_at: new Date().toISOString(),
          };
          const next = [item, ...state.notifications].slice(
            0,
            MAX_NOTIFICATIONS,
          );
          return {
            notifications: next,
            unreadCount: next.filter((x) => !x.read).length,
          };
        }),

      markRead: (id) =>
        set((state) => {
          const notifications = state.notifications.map((n) =>
            n.id === id ? { ...n, read: true } : n,
          );
          return {
            notifications,
            unreadCount: notifications.filter((x) => !x.read).length,
          };
        }),

      markAllRead: () =>
        set((state) => ({
          notifications: state.notifications.map((n) => ({
            ...n,
            read: true,
          })),
          unreadCount: 0,
        })),

      clearAll: () =>
        set(() => ({
          notifications: [],
          unreadCount: 0,
        })),
    }),
    {
      name: "bb-notifications",
      partialize: (state) => ({
        notifications: state.notifications,
        unreadCount: state.unreadCount,
      }),
    },
  ),
);
