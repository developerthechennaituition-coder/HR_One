import { useEffect, useRef, useState } from "react";

import notificationSound from "../assets/notification.wav";
import { getNotificationSnapshot } from "../services/notificationApi";

function NotificationBell({ onNavigate }) {
  const [unreadCount, setUnreadCount] = useState(0);
  const previousIdsRef = useRef(null);
  const audioRef = useRef(null);
  const audioUnlockedRef = useRef(false);
  const pendingSoundsRef = useRef(0);
  const snapshotInFlightRef = useRef(false);
  const soundPlayingRef = useRef(false);

  useEffect(() => {
    // Create the media element synchronously so an early user gesture is not
    // lost while an asynchronous Blob fetch is still in progress.
    const audio = new Audio(notificationSound);
    audio.preload = "auto";
    audio.volume = 1;
    audioRef.current = audio;
    audio.load();

    return () => {
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
      audioRef.current = null;
    };
  }, []);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return undefined;

    const playNextQueuedSound = () => {
      if (!audioUnlockedRef.current || soundPlayingRef.current || pendingSoundsRef.current <= 0) return;
      soundPlayingRef.current = true;
      audio.currentTime = 0;
      const playback = audio.play();
      if (playback && typeof playback.then === "function") {
        playback.then(() => {
          pendingSoundsRef.current = Math.max(0, pendingSoundsRef.current - 1);
        }).catch((error) => {
          soundPlayingRef.current = false;
          audioUnlockedRef.current = false;
          console.warn("Notification sound playback was blocked:", error);
        });
      } else {
        pendingSoundsRef.current = Math.max(0, pendingSoundsRef.current - 1);
      }
    };

    const onEnded = () => {
      soundPlayingRef.current = false;
      playNextQueuedSound();
    };
    const onAudioError = () => {
      soundPlayingRef.current = false;
      audioUnlockedRef.current = false;
    };
    audio.addEventListener("ended", onEnded);
    audio.addEventListener("error", onAudioError);

    const events = ["click", "keydown", "touchstart"];
    const unlockAudio = () => {
      if (audioUnlockedRef.current) return;
      // A muted play/pause initiated directly by a user gesture unlocks
      // subsequent notification playback in browsers with autoplay policy.
      audio.muted = true;
      const unlock = audio.play();
      if (unlock && typeof unlock.then === "function") {
        unlock.then(() => {
          audio.pause();
          audio.currentTime = 0;
          audio.muted = false;
          audioUnlockedRef.current = true;
          playNextQueuedSound();
        }).catch(() => {
          audio.muted = false;
        });
      }
    };
    events.forEach((name) => window.addEventListener(name, unlockAudio, { passive: true }));

    return () => {
      audio.removeEventListener("ended", onEnded);
      audio.removeEventListener("error", onAudioError);
      events.forEach((name) => window.removeEventListener(name, unlockAudio));
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    let intervalId = null;
    let authenticationExpired = false;

    const loadSnapshot = async () => {
      if (authenticationExpired || snapshotInFlightRef.current) return;
      snapshotInFlightRef.current = true;
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
        // First load establishes a baseline: old inbox items never make a sound.
        if (previousIds !== null) {
          const newCount = [...currentIds].filter((id) => !previousIds.has(id)).length;
          if (newCount > 0) {
            pendingSoundsRef.current += newCount;
            const audio = audioRef.current;
            if (audio && audioUnlockedRef.current && !soundPlayingRef.current) {
              audio.currentTime = 0;
              soundPlayingRef.current = true;
              const playback = audio.play();
              if (playback && typeof playback.then === "function") {
                playback.then(() => {
                  pendingSoundsRef.current = Math.max(0, pendingSoundsRef.current - 1);
                }).catch((error) => {
                  soundPlayingRef.current = false;
                  audioUnlockedRef.current = false;
                  console.warn("Notification sound playback failed:", error);
                });
              }
            }
          }
        }
        previousIdsRef.current = currentIds;
      } catch (error) {
        if (error?.status === 401) {
          authenticationExpired = true;
          if (intervalId !== null) window.clearInterval(intervalId);
          window.dispatchEvent(new CustomEvent("hrms-authentication-expired"));
          return;
        }
        console.error("Unable to load notification snapshot:", error);
      } finally {
        snapshotInFlightRef.current = false;
      }
    };

    loadSnapshot();
    intervalId = window.setInterval(loadSnapshot, 3000);
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
