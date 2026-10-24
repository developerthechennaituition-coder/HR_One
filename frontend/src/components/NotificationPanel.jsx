import { useEffect, useState } from "react";

import {
  getNotifications,
  markNotificationViewed,
  acknowledgeNotification,
  deleteNotification,
  getUnreadNotificationCount,
} from "../services/notificationApi";

function NotificationPanel({
  user = {},
  onClose,
  onCountChange,
  canManageNotifications = false,
  onCreateNotification,
}) {
  const [notifications, setNotifications] =
    useState([]);

  const [loading, setLoading] = useState(true);

  const [error, setError] = useState("");

  const [processingId, setProcessingId] =
    useState(null);

  /**
   * Format date/time for display.
   */
  const formatDateTime = (value) => {
    if (!value) {
      return "-";
    }

    const date = new Date(value);

    if (Number.isNaN(date.getTime())) {
      return "-";
    }

    return date.toLocaleString();
  };

  /**
   * Load notifications.
   */
  const loadNotifications = async () => {
    try {
      setLoading(true);
      setError("");

      const data = await getNotifications();

      const notificationList =
        Array.isArray(data)
          ? data
          : data?.notifications || [];

      setNotifications(notificationList);
    } catch (err) {
      console.error(
        "Unable to load notifications:",
        err
      );

      setError(
        err.message ||
          "Unable to load notifications."
      );
    } finally {
      setLoading(false);
    }
  };

  /**
   * Load notifications when panel opens.
   */
  useEffect(() => {
    loadNotifications();
  }, []);

  /**
   * Refresh unread count.
   */
  const refreshUnreadCount = async () => {
    try {
      const data =
        await getUnreadNotificationCount();

      const count = Number(
        data?.unread_count || 0
      );

      if (onCountChange) {
        onCountChange(count);
      }
    } catch (err) {
      console.error(
        "Unable to refresh unread count:",
        err
      );
    }
  };

  /**
   * View notification.
   *
   * Important:
   * View != Read.
   */
  const handleViewNotification = async (
    notification
  ) => {
    try {
      setProcessingId(notification.id);

      const result =
        await markNotificationViewed(
          notification.id
        );

      setNotifications((current) =>
        current.map((item) =>
          item.id === notification.id
            ? {
                ...item,
                is_viewed: true,
                viewed_at:
                  result?.viewed_at ||
                  new Date().toISOString(),
              }
            : item
        )
      );
    } catch (err) {
      console.error(
        "Unable to mark notification as viewed:",
        err
      );

      alert(
        err.message ||
          "Unable to view notification."
      );
    } finally {
      setProcessingId(null);
    }
  };

  /**
   * Acknowledge notification.
   *
   * User must view it first.
   */
  const handleAcknowledge = async (
    notification
  ) => {
    if (!notification.is_viewed) {
      return;
    }

    try {
      setProcessingId(notification.id);

      const result =
        await acknowledgeNotification(
          notification.id
        );

      setNotifications((current) =>
        current.map((item) =>
          item.id === notification.id
            ? {
                ...item,
                is_acknowledged: true,
                acknowledged_at:
                  result?.acknowledged_at ||
                  new Date().toISOString(),
              }
            : item
        )
      );

      if (
        result?.unread_count !== undefined
      ) {
        onCountChange?.(
          Number(result.unread_count)
        );
      } else {
        await refreshUnreadCount();
      }
    } catch (err) {
      console.error(
        "Unable to acknowledge notification:",
        err
      );

      alert(
        err.message ||
          "Unable to mark notification as read."
      );
    } finally {
      setProcessingId(null);
    }
  };

  /**
   * Delete own notification.
   */
  const handleDelete = async (
    notification
  ) => {
    const confirmed = window.confirm(
      "Are you sure you want to delete this notification?"
    );

    if (!confirmed) {
      return;
    }

    try {
      setProcessingId(notification.id);

      await deleteNotification(
        notification.id
      );

      setNotifications((current) =>
        current.filter(
          (item) =>
            item.id !== notification.id
        )
      );

      await refreshUnreadCount();
    } catch (err) {
      console.error(
        "Unable to delete notification:",
        err
      );

      alert(
        err.message ||
          "Unable to delete notification."
      );
    } finally {
      setProcessingId(null);
    }
  };

  /**
   * Render update history.
   */
  const renderUpdates = (notification) => {
    if (
      !Array.isArray(notification.updates) ||
      notification.updates.length === 0
    ) {
      return null;
    }

    return (
      <div className="mt-3">
        <div className="fw-semibold small mb-2">
          Update History
        </div>

        {notification.updates.map(
          (update, index) => (
            <div
              key={index}
              className="border rounded p-2 mb-2 bg-light"
            >
              <div className="small text-muted mb-2">
                Updated by{" "}
                <strong>
                  {update.changed_by_name ||
                    "User"}
                </strong>{" "}
                on{" "}
                {formatDateTime(
                  update.changed_at
                )}
              </div>

              {Array.isArray(
                update.changes
              ) &&
                update.changes.map(
                  (change, changeIndex) => (
                    <div
                      key={changeIndex}
                      className="small mb-1"
                    >
                      <strong>
                        {change.field}
                      </strong>
                      :{" "}
                      <span className="text-muted">
                        {String(
                          change.old_value ?? ""
                        )}
                      </span>
                      {" → "}
                      <span>
                        {String(
                          change.new_value ?? ""
                        )}
                      </span>
                    </div>
                  )
                )}
            </div>
          )
        )}
      </div>
    );
  };

  return (
      <div
      className="notification-panel"
      style={{
        position: "absolute",
        top: "calc(100% + 12px)",
        right: 0,
        width: "390px",
        maxWidth: "calc(100vw - 24px)",
        backgroundColor: "#ffffff",
        borderRadius: "8px",
        boxShadow: "0 8px 25px rgba(0,0,0,0.15)",
        zIndex: 2000,
        overflow: "hidden",
        boxSizing: "border-box",
      }}
    >
      {/* Panel Header */}
      <div
        className="notification-panel-header"
        style={{
          padding: "14px 16px",
          borderBottom:
            "1px solid #e9ecef",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "10px",
        }}
      >
        <div>
          <h6 className="mb-0">
            Notifications
          </h6>

          <small className="text-muted">
            Your latest notifications
          </small>
        </div>

        <div className="d-flex align-items-center gap-2">
          {/* Create Notification */}
          {canManageNotifications && (
            <button
              type="button"
              className="btn btn-sm btn-primary"
              onClick={
                onCreateNotification
              }
              title="Create Notification"
            >
              <i className="bi bi-plus-lg me-1" />
              Create
            </button>
          )}

          {/* Close */}
          <button
            type="button"
            className="btn btn-sm btn-light"
            onClick={onClose}
            aria-label="Close notifications"
          >
            <i className="bi bi-x-lg" />
          </button>
        </div>
      </div>

      {/* Content */}
      <div
        className="notification-panel-body"
        style={{
          maxHeight: "500px",
          overflowY: "auto",
        }}
      >
        {/* Loading */}
        {loading && (
          <div className="text-center py-5">
            <div
              className="spinner-border spinner-border-sm"
              role="status"
            />

            <div className="small text-muted mt-2">
              Loading notifications...
            </div>
          </div>
        )}

        {/* Error */}
        {!loading && error && (
          <div className="p-4 text-center">
            <i className="bi bi-exclamation-circle text-danger fs-3" />

            <p className="text-danger small mt-2 mb-3">
              {error}
            </p>

            <button
              type="button"
              className="btn btn-sm btn-outline-primary"
              onClick={loadNotifications}
            >
              Try Again
            </button>
          </div>
        )}

        {/* Empty */}
        {!loading &&
          !error &&
          notifications.length === 0 && (
            <div className="text-center py-5 px-3">
              <i
                className="bi bi-bell-slash"
                style={{
                  fontSize: "42px",
                  color: "#6c757d",
                }}
              />

              <p className="text-muted mt-3 mb-0">
                No notifications
              </p>

              {canManageNotifications && (
                <p className="small text-muted mt-2 mb-0">
                  You can create a new notification
                  using the <strong>Create</strong>{" "}
                  button above.
                </p>
              )}
            </div>
          )}

        {/* Notification List */}
        {!loading &&
          !error &&
          notifications.length > 0 && (
            <div>
              {notifications.map(
                (notification) => {
                  const isProcessing =
                    processingId ===
                    notification.id;

                  const isNew =
                    !notification.is_acknowledged;

                  return (
                    <div
                      key={notification.id}
                      className="notification-item"
                      style={{
                        padding: "16px",
                        borderBottom:
                          "1px solid #eeeeee",
                        backgroundColor:
                          isNew
                            ? "#f8fbff"
                            : "#ffffff",
                      }}
                    >
                      {/* Title */}
                      <div className="d-flex justify-content-between align-items-start gap-2">
                        <div>
                          <div className="fw-semibold">
                            {notification.title}
                          </div>

                          {isNew && (
                            <span className="badge bg-primary mt-1">
                              New
                            </span>
                          )}
                        </div>

                        {/* Delete own notification */}
                        {notification.can_delete && (
                          <button
                            type="button"
                            className="btn btn-sm btn-outline-danger"
                            disabled={
                              isProcessing
                            }
                            onClick={() =>
                              handleDelete(
                                notification
                              )
                            }
                            title="Delete notification"
                          >
                            <i className="bi bi-trash" />
                          </button>
                        )}
                      </div>

                      {/* Creator */}
                      <div className="small text-muted mt-2">
                        Posted by{" "}
                        <strong>
                          {
                            notification.created_by_name
                          }
                        </strong>

                        {notification.created_by_role && (
                          <>
                            {" "}
                            ·{" "}
                            {
                              notification.created_by_role
                            }
                          </>
                        )}
                      </div>

                      {/* Message */}
                      <div className="mt-2">
                        {notification.message}
                      </div>

                      {/* Dates */}
                      <div className="small text-muted mt-2">
                        <div>
                          Created:{" "}
                          {formatDateTime(
                            notification.created_at
                          )}
                        </div>
        
                        <div>
                          Expires:{" "}
                          {formatDateTime(
                            notification.expires_at
                          )}
                        </div>
                      </div>

                      {/* Viewer count */}
                      {notification.viewer_count !==
                        undefined && (
                        <div className="small text-muted mt-2">
                          <i className="bi bi-eye me-1" />

                          {
                            notification.viewer_count
                          }{" "}
                          viewer
                          {notification.viewer_count !==
                          1
                            ? "s"
                            : ""}
                        </div>
                      )}

                      {/* Update History */}
                      {renderUpdates(
                        notification
                      )}

                      {/* Actions */}
                      <div className="d-flex align-items-center gap-2 mt-3">
                        {/* View */}
                        {!notification.is_viewed && (
                          <button
                            type="button"
                            className="btn btn-sm btn-outline-primary"
                            disabled={
                              isProcessing
                            }
                            onClick={() =>
                              handleViewNotification(
                                notification
                              )
                            }
                          >
                            <i className="bi bi-eye me-1" />
                            View Notification
                          </button>
                        )}

                        {/* Already Viewed */}
                        {notification.is_viewed &&
                          !notification.is_acknowledged && (
                            <button
                              type="button"
                              className="btn btn-sm btn-success"
                              disabled={
                                isProcessing
                              }
                              onClick={() =>
                                handleAcknowledge(
                                  notification
                                )
                              }
                            >
                              <i className="bi bi-check-lg me-1" />
                              Mark as read
                            </button>
                          )}

                        {/* Already acknowledged */}
                        {notification.is_acknowledged && (
                          <span className="small text-success">
                            <i className="bi bi-check-circle-fill me-1" />
                            Read
                          </span>
                        )}
                      </div>
                    </div>
                  );
                }
              )}
            </div>
          )}
      </div>
    </div>
  );
}

export default NotificationPanel;