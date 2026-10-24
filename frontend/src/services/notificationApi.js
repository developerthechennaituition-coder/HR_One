import { HRONE_API_BASE } from "../config/api.js";
const API_BASE_URL = HRONE_API_BASE;

/**
 * Get current HRMS session from sessionStorage.
 */
function getSession() {
  const rawSession = sessionStorage.getItem("hrms_session");

  if (!rawSession) {
    return null;
  }

  try {
    return JSON.parse(rawSession);
  } catch (error) {
    console.error(
      "Unable to parse HRMS session:",
      error
    );

    return null;
  }
}

/**
 * Get authentication token from current session.
 */
export function getAuthToken() {
  const session = getSession();

  if (!session) {
    return null;
  }

  return (
    session.token ||
    session.access_token ||
    session.session_token ||
    null
  );
}

/**
 * Common notification API request helper.
 */
async function notificationRequest(
  endpoint,
  options = {}
) {
  const token = getAuthToken();

  if (!token) {
    throw new Error(
      "Your session has expired. Please login again."
    );
  }

  const response = await fetch(
    `${API_BASE_URL}${endpoint}`,
    {
      ...options,

      headers: {
        "Content-Type": "application/json",
        ...(options.headers || {}),
        Authorization: `Bearer ${token}`,
      },
    }
  );

  let data = null;

  try {
    data = await response.json();
  } catch {
    data = null;
  }

  if (!response.ok) {
    const errorMessage =
      data?.detail ||
      data?.message ||
      "Notification request failed.";

    throw new Error(errorMessage);
  }

  return data;
}

/**
 * Get notifications visible to current user.
 */
export async function getNotifications() {
  return notificationRequest(
    "/api/notifications"
  );
}

/**
 * Get unread notification count.
 */
export async function getUnreadNotificationCount() {
  return notificationRequest(
    "/api/notifications/unread-count"
  );
}

export async function getNotificationSnapshot() {
  return notificationRequest("/api/notifications/snapshot");
}

export async function getNotificationLookups() {
  return notificationRequest("/api/notifications/lookups");
}

/**
 * Mark notification as viewed.
 *
 * Viewing does NOT mark it as read.
 */
export async function markNotificationViewed(
  notificationId
) {
  return notificationRequest(
    `/api/notifications/${notificationId}/view`,
    {
      method: "POST",
    }
  );
}

/**
 * Acknowledge/read a notification.
 */
export async function acknowledgeNotification(
  notificationId
) {
  return notificationRequest(
    `/api/notifications/${notificationId}/acknowledge`,
    {
      method: "POST",
    }
  );
}

export async function starNotification(notificationId) {
  return notificationRequest(`/api/notifications/${notificationId}/star`, {
    method: "POST",
  });
}

export async function unstarNotification(notificationId) {
  return notificationRequest(`/api/notifications/${notificationId}/unstar`, {
    method: "POST",
  });
}

export async function markLeaveNotificationRead(notificationId) {
  return notificationRequest(`/api/notifications/leave/${notificationId}/read`, {
    method: "POST",
  });
}

export async function starLeaveNotification(notificationId) {
  return notificationRequest(`/api/notifications/leave/${notificationId}/star`, {
    method: "POST",
  });
}

export async function unstarLeaveNotification(notificationId) {
  return notificationRequest(`/api/notifications/leave/${notificationId}/unstar`, {
    method: "POST",
  });
}

export async function deleteLeaveNotification(notificationId) {
  return notificationRequest(`/api/notifications/leave/${notificationId}`, {
    method: "DELETE",
  });
}

/**
 * Get viewers of a notification.
 *
 * Creator-only backend endpoint.
 */
export async function getNotificationViewers(
  notificationId
) {
  return notificationRequest(
    `/api/notifications/${notificationId}/viewers`
  );
}

/**
 * Create a new notification.
 */
export async function createNotification(
  notificationData
) {
  return notificationRequest(
    "/api/notifications",
    {
      method: "POST",
      body: JSON.stringify(notificationData),
    }
  );
}

/**
 * Update own notification.
 */
export async function updateNotification(
  notificationId,
  notificationData
) {
  return notificationRequest(
    `/api/notifications/${notificationId}`,
    {
      method: "PUT",
      body: JSON.stringify(notificationData),
    }
  );
}

/**
 * Delete own notification.
 */
export async function deleteNotification(
  notificationId
) {
  return notificationRequest(
    `/api/notifications/${notificationId}`,
    {
      method: "DELETE",
    }
  );
}

export async function holdNotification(notificationId) {
  return notificationRequest(`/api/notifications/${notificationId}/hold`, {
    method: "POST",
  });
}

export async function resumeNotification(notificationId) {
  return notificationRequest(`/api/notifications/${notificationId}/resume`, {
    method: "POST",
  });
}