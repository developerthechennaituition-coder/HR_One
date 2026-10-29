import { useEffect, useRef, useState } from "react";

import notificationSound from "../assets/notification.wav";
import { getNotificationSnapshot } from "../services/notificationApi";

function NotificationBell({ onNavigate }) {
  const [unreadCount, setUnreadCount] = useState(0);
  const previousIdsRef = useRef(null);
  const audioRef = useRef(null);
  const audioUnlockedRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    let objectUrl = null;

    // Load the sound as one in-memory Blob. Streaming it straight into
    // <audio> uses HTTP range requests, which fail in some cache setups
    // (net::ERR_CACHE_OPERATION_NOT_SUPPORTED) and leave no playable source.
    fetch(notificationSound)
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.blob();
      })
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        const audio = new Audio(objectUrl);
        audio.preload = "auto";
        audio.volume = 1;
        audioRef.current = audio;
      })
      .catch((error) => {
        if (!cancelled) console.warn("Notification sound could not be loaded:", error);
      });

    return () => {
      cancelled = true;
      audioRef.current?.pause();
      audioRef.current = null;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, []);

  useEffect(() => {
    const events = ["click", "keydown", "touchstart"];
    const removeListeners = () => {
      events.forEach((name) => window.removeEventListener(name, unlockAudio));
    };

    // Browsers allow audio only after a user gesture. Retry on each gesture
    // until it succeeds (the sound may still be loading on the first one).
    function unlockAudio() {
      const audio = audioRef.current;
      if (!audio || audioUnlockedRef.current) return;

      audio.muted = true;
      audio.play()
        .then(() => {
          audio.pause();
          audio.currentTime = 0;
          audio.muted = false;
          audioUnlockedRef.current = true;
          removeListeners();
        })
        .catch(() => {
          audio.muted = false;
        });
    }

    events.forEach((name) => window.addEventListener(name, unlockAudio));
    return removeListeners;
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
