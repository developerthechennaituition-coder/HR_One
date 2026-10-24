import { useEffect, useRef, useState } from "react";

import notificationSound from "../assets/notification.wav";
import { getNotificationSnapshot } from "../services/notificationApi";

function NotificationBell({ onNavigate }) {
  const [unreadCount, setUnreadCount] = useState(0);
  const previousIdsRef = useRef(null);
  const audioRef = useRef(null);
  const audioUnlockedRef = useRef(false);

  useEffect(() => {
    const audio = new Audio(notificationSound);
    audio.preload = "auto";
    audio.volume = 1;
    audioRef.current = audio;

    return () => {
      audio.pause();
      audio.currentTime = 0;
      audioRef.current = null;
    };
  }, []);

  useEffect(() => {
    const unlockAudio = () => {
      const audio = audioRef.current;
      if (!audio || audioUnlockedRef.current) return;

      audio.play()
        .then(() => {
          audio.pause();
          audio.currentTime = 0;
          audioUnlockedRef.current = true;
        })
        .catch((error) => {
          console.warn("Notification sound could not be unlocked:", error);
        });
    };

    window.addEventListener("click", unlockAudio, { once: true });
    window.addEventListener("keydown", unlockAudio, { once: true });
    window.addEventListener("touchstart", unlockAudio, { once: true });

    return () => {
      window.removeEventListener("click", unlockAudio);
      window.removeEventListener("keydown", unlockAudio);
      window.removeEventListener("touchstart", unlockAudio);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;

    const playNotificationSound = () => {
      const audio = audioRef.current;
      if (!audio || !audioUnlockedRef.current) {
        if (!audioUnlockedRef.current) {
          console.warn("Notification sound is waiting for user interaction.");
        }
        return;
      }

      audio.currentTime = 0;
      audio.play().catch((error) => {
        console.error("Notification sound playback failed:", error);
      });
    };

    const loadSnapshot = async () => {
      try {
        const snapshot = await getNotificationSnapshot();
        if (cancelled) return;

        const ids = [
          ...(Array.isArray(snapshot?.notification_ids) ? snapshot.notification_ids : []),
          ...(Array.isArray(snapshot?.leave_notification_ids) ? snapshot.leave_notification_ids : []),
        ].filter(Boolean);
        const currentIds = new Set(ids);
        const previousIds = previousIdsRef.current;

        setUnreadCount(Number(snapshot?.unread_count || 0));
        if (previousIds !== null) {
          const hasNewNotification = [...currentIds].some((id) => !previousIds.has(id));
          if (hasNewNotification) playNotificationSound();
        }
        previousIdsRef.current = currentIds;
      } catch (error) {
        console.error("Unable to load notification snapshot:", error);
      }
    };

    loadSnapshot();
    const intervalId = window.setInterval(loadSnapshot, 3000);
    window.addEventListener("hrms-notifications-updated", loadSnapshot);

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
      window.removeEventListener("hrms-notifications-updated", loadSnapshot);
    };
  }, []);

  return (
    <div className="notification-wrapper" style={{ position: "relative", overflow: "visible" }}>
      <button
        type="button"
        className="notification-btn"
        aria-label="Open Notifications"
        onClick={() => onNavigate?.("notifications")}
      >
        <i className="bi bi-bell" aria-hidden="true" />
        {unreadCount > 0 && (
          <span className="notification-badge">
            {unreadCount > 99 ? "99+" : unreadCount}
          </span>
        )}
      </button>
    </div>
  );
}

export default NotificationBell;
