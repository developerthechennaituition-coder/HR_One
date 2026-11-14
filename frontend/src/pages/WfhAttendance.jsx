import { useCallback, useEffect, useRef, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import { HRONE_API_BASE } from "../config/api.js";
import { formatIST } from "../services/attendanceHistoryFormat";

// Use the shared API base (same source as notifications, attendance, leave and
// payroll). The page previously derived its own base from the browser's
// protocol/host, which produced https://<LAN-IP>:8000 against a plain-HTTP
// backend and caused "Failed to fetch" / "Invalid HTTP request received".
const API_BASE_URL = HRONE_API_BASE;
const CONNECTION_ERROR_MESSAGE = "Unable to connect to the attendance server.";
const NOT_AUTHORIZED_MESSAGE = "Your account is not authorized.";
const WFH_DISABLED_MESSAGE = "WFH attendance is not enabled.";
const configuredPublicFrontendUrl = (import.meta.env.VITE_PUBLIC_FRONTEND_URL || "").trim();
const configuredFrontendIsLocal = /^(https?:\/\/)?(localhost|127\.0\.0\.1)(:\d+)?$/i.test(
  configuredPublicFrontendUrl.replace(/\/+$/, ""),
);
const PUBLIC_FRONTEND_URL = (
  !configuredPublicFrontendUrl || configuredFrontendIsLocal
    ? window.location.origin
    : configuredPublicFrontendUrl
).replace(/\/+$/, "");
const LOCATION_TIMEOUT_MS = 30000;
const LOCATION_REFINEMENT_MS = 2500;
// Re-read GPS before the final submit if the earlier reading is older than this
// (the server accepts readings up to 120 s old by default).
const LOCATION_REUSE_LIMIT_MS = 45000;
const DEFAULT_MAX_WFH_LOCATION_ACCURACY_METERS = 100;

const formatTime = (time) => {
  if (!time) return "Not Marked";
  const [hourText, minute] = time.split(":");
  const hour = Number(hourText);
  return `${hour % 12 || 12}:${minute} ${hour >= 12 ? "PM" : "AM"}`;
};

const WFH_FIELD_LABELS = {
  latitude: "Latitude",
  longitude: "Longitude",
  accuracy_m: "GPS accuracy",
  location_timestamp: "Location time",
  purpose: "Action",
  challenge_id: "Face verification session",
  frames: "Face capture",
};

const formatApiError = (detail) => {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === "string") return item;
        if (!item || typeof item !== "object") return "";
        const field = Array.isArray(item.loc)
          ? item.loc.filter((part) => part !== "body").join(".")
          : "";
        const location = WFH_FIELD_LABELS[field] || field;
        const message = typeof item.msg === "string" ? item.msg : "";
        return [location, message].filter(Boolean).join(": ");
      })
      .filter(Boolean)
      .join(" ");
  }
  if (detail && typeof detail === "object") {
    if (typeof detail.message === "string") return detail.message;
    if (typeof detail.msg === "string") return detail.msg;
  }
  return "";
};

function WfhAttendance({ session, fallbackPurpose = null }) {
  const token = session?.token;
  const [settings, setSettings] = useState(null);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [workingMessage, setWorkingMessage] = useState("");
  const [locationResult, setLocationResult] = useState(null);
  const [cameraReady, setCameraReady] = useState(false);
  const [error, setError] = useState("");
  // Failure to load WFH status (network / authorization). Kept separate from
  // `error` so a failed request is never rendered as "WFH not enabled".
  const [statusError, setStatusError] = useState(null);
  const [notice, setNotice] = useState("");
  const [fallbackRequestPurpose, setFallbackRequestPurpose] = useState(null);
  const [showFallbackOptions, setShowFallbackOptions] = useState(false);
  const [fallbackEmailSent, setFallbackEmailSent] = useState(false);
  const [qrVerificationUrl, setQrVerificationUrl] = useState("");
  const [qrExpiresInMinutes, setQrExpiresInMinutes] = useState(5);
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const workingRef = useRef(false);
  // server clock minus device clock (ms); aligns location timestamps with the server.
  const serverClockOffsetRef = useRef(0);
  const locationCancelRef = useRef(null);
  const mountedRef = useRef(true);
  const loadInFlightRef = useRef(null);

  const headers = useCallback(() => {
    if (!token) {
      throw new Error("Login session not found. Please login again.");
    }
    return {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    };
  }, [token]);

  const apiRequest = useCallback(
    async (path, options = {}) => {
      let response;
      const requestStartedAt = Date.now();
      try {
        response = await fetch(`${API_BASE_URL}/api/attendance${path}`, {
          cache: "no-store",
          ...options,
          headers: { ...headers(), ...options.headers },
        });
      } catch (networkError) {
        // fetch() rejects with a TypeError only when no HTTP response was received.
        if (networkError?.name === "AbortError") throw networkError;
        const connectionError = new Error(CONNECTION_ERROR_MESSAGE);
        connectionError.isNetworkError = true;
        throw connectionError;
      }
      const data = await response.json().catch(() => ({}));
      if (response.ok && Number.isFinite(data?.server_time_ms)) {
        // Assume the server stamped the response mid-flight.
        const deviceMidpoint = (requestStartedAt + Date.now()) / 2;
        serverClockOffsetRef.current = data.server_time_ms - deviceMidpoint;
      }
      if (!response.ok) {
        const requestError = new Error(
          response.status === 401
            ? "Your session has expired. Please login again."
            : formatApiError(data?.detail) ||
              (response.status === 422
                ? "The location details could not be validated. Allow location access, wait for a fresh GPS reading and try again."
                : "Unable to complete WFH attendance."),
        );
        requestError.status = response.status;
        throw requestError;
      }
      return data;
    },
    [headers],
  );

  const loadSettings = useCallback(() => {
    // Share one in-flight request so Refresh Status, the polling timer and
    // React StrictMode's double effect never fire duplicate /wfh/me calls.
    if (loadInFlightRef.current) return loadInFlightRef.current;
    const request = (async () => {
      try {
        const latest = await apiRequest("/wfh/me");
        if (!mountedRef.current) return;
        setSettings(latest);
        setStatusError(null);
      } catch (loadError) {
        if (!mountedRef.current) return;
        if (loadError.isNetworkError) {
          setStatusError({ type: "network", message: CONNECTION_ERROR_MESSAGE });
        } else if (loadError.status === 403) {
          setSettings(null);
          setStatusError({ type: "unauthorized", message: NOT_AUTHORIZED_MESSAGE });
        } else {
          setStatusError({
            type: "error",
            message: loadError.message || "Unable to load WFH attendance.",
          });
        }
      } finally {
        loadInFlightRef.current = null;
        if (mountedRef.current) setLoading(false);
      }
    })();
    loadInFlightRef.current = request;
    return request;
  }, [apiRequest]);

  useEffect(() => {
    loadSettings();
  }, [loadSettings]);

  // Revalidate while a late check-in request awaits a decision or an approved
  // extension is running, so approval/expiry is reflected without a manual refresh.
  const latePermissionStatus = settings?.late_check_in_permission?.status;
  const awaitingLateCheckIn =
    !settings?.attendance_already_marked &&
    (latePermissionStatus === "pending" || latePermissionStatus === "approved");
  useEffect(() => {
    if (!awaitingLateCheckIn) return undefined;
    const timer = window.setInterval(() => {
      if (!workingRef.current) loadSettings();
    }, 30000);
    return () => window.clearInterval(timer);
  }, [awaitingLateCheckIn, loadSettings]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      locationCancelRef.current?.();
      streamRef.current?.getTracks().forEach((track) => track.stop());
    };
  }, []);

  // Location fields for the API. location_age_ms is measured with the device's
  // monotonic timer at the moment of sending, so it is independent of the
  // device and server clocks (which can differ by minutes).
  const locationFields = (location) => ({
    latitude: location.latitude,
    longitude: location.longitude,
    accuracy_m: location.accuracy,
    location_timestamp: location.timestamp,
    ...(Number.isFinite(location.receivedPerf)
      ? { location_age_ms: Math.max(0, Math.round(performance.now() - location.receivedPerf)) }
      : {}),
  });

  // A reading can legitimately go stale (slow network, phone woke from sleep).
  // When the server reports that, take one new live reading and resend; the
  // server still applies every freshness, accuracy and radius check.
  const isOutdatedLocationError = (error) =>
    error?.status === 422 && /outdated/i.test(error?.message || "");

  const sendWithFreshLocation = async (send, location, maxAccuracy) => {
    try {
      return { result: await send(location), location };
    } catch (error) {
      if (!isOutdatedLocationError(error)) throw error;
      setWorkingMessage("Taking a fresh location reading...");
      const refreshed = await detectLocation(setWorkingMessage, maxAccuracy);
      return { result: await send(refreshed), location: refreshed };
    }
  };

  const detectLocation = (onAccuracyUpdate, maxAccuracy) =>
    new Promise((resolve, reject) => {
      if (!window.isSecureContext) {
        reject(
          new Error(
            "Location and camera access require HTTPS or localhost. Open HRMS using a secure connection.",
          ),
        );
        return;
      }
      if (!navigator.geolocation) {
        reject(new Error("This browser does not support location detection."));
        return;
      }
      let watchId;
      let timeoutId;
      let refinementId;
      let bestPosition = null;
      let bestAccuracy = Number.POSITIVE_INFINITY;
      // Time (on the server's clock) at which the best reading was received.
      // The position's own timestamp can be minutes old on desktop location
      // providers, and device clocks can be wrong; both made fresh readings
      // look "outdated". maximumAge: 0 guarantees the reading itself is fresh.
      let bestReceivedAt = Date.now() + serverClockOffsetRef.current;
      let bestReceivedPerf = performance.now();
      const accuracyLimit =
        Number.isFinite(maxAccuracy) && maxAccuracy > 0
          ? maxAccuracy
          : DEFAULT_MAX_WFH_LOCATION_ACCURACY_METERS;
      let positionUnavailable = false;
      let settled = false;

      const finish = (callback, value) => {
        if (settled) return;
        settled = true;
        window.clearTimeout(timeoutId);
        window.clearTimeout(refinementId);
        if (watchId !== undefined) {
          navigator.geolocation.clearWatch(watchId);
        }
        if (locationCancelRef.current === cancel) {
          locationCancelRef.current = null;
        }
        callback(value);
      };

      const cancel = () =>
        finish(reject, new Error("Location verification was cancelled."));
      locationCancelRef.current = cancel;

      timeoutId = window.setTimeout(() => {
        if (!bestPosition) {
          finish(
            reject,
            new Error(
              positionUnavailable
                ? "Your current location could not be detected. Please enable Windows Location Services and allow location access in your browser."
                : "We could not obtain your location in time. Please enable Windows Location Services and allow location access in your browser, then try again.",
            ),
          );
          return;
        }

        if (bestAccuracy > accuracyLimit) {
          finish(
            reject,
            new Error(
              `Your device reported location accuracy of ±${Math.round(bestAccuracy)} m, which is not precise enough (maximum allowed ±${Math.round(accuracyLimit)} m). Ethernet does not provide physical coordinates by itself. Enable Windows Location Services and browser location access, retry, or use an approved GPS-capable mobile device. Manual home coordinates cannot replace your live attendance location.`,
            ),
          );
          return;
        }

        finish(resolve, {
          latitude: bestPosition.coords.latitude,
          longitude: bestPosition.coords.longitude,
          accuracy: bestPosition.coords.accuracy,
          timestamp: bestReceivedAt,
          receivedPerf: bestReceivedPerf,
        });
      }, LOCATION_TIMEOUT_MS);

      try {
        watchId = navigator.geolocation.watchPosition(
          (position) => {
            const { coords } = position;
            if (
              !Number.isFinite(coords.latitude) ||
              coords.latitude < -90 ||
              coords.latitude > 90 ||
              !Number.isFinite(coords.longitude) ||
              coords.longitude < -180 ||
              coords.longitude > 180 ||
              !Number.isFinite(coords.accuracy) ||
              coords.accuracy <= 0 ||
              !Number.isFinite(position.timestamp) ||
              position.timestamp <= 0
            ) {
              return;
            }
            if (coords.accuracy < bestAccuracy) {
              bestPosition = position;
              bestAccuracy = coords.accuracy;
              bestReceivedAt = Date.now() + serverClockOffsetRef.current;
              bestReceivedPerf = performance.now();
            }
            onAccuracyUpdate?.(
              "Location reading received. Waiting briefly for a more accurate fresh reading...",
            );

            if (
              bestAccuracy <= accuracyLimit &&
              refinementId === undefined
            ) {
              refinementId = window.setTimeout(() => {
                if (!bestPosition) return;
                finish(resolve, {
                  latitude: bestPosition.coords.latitude,
                  longitude: bestPosition.coords.longitude,
                  accuracy: bestPosition.coords.accuracy,
                  timestamp: bestReceivedAt,
                  receivedPerf: bestReceivedPerf,
                });
              }, LOCATION_REFINEMENT_MS);
            }
          },
          (locationError) => {
            if (locationError.code === locationError.PERMISSION_DENIED) {
              finish(
                reject,
                new Error(
                  "Location permission is required for WFH attendance. Enable Windows Location Services and allow location access for this site in your browser. If this Ethernet-connected desktop cannot provide a precise location, use an approved GPS-capable device.",
                ),
              );
            } else if (locationError.code === locationError.POSITION_UNAVAILABLE) {
              positionUnavailable = true;
              onAccuracyUpdate?.(
                "Your device has not determined a location yet. Waiting for another reading...",
              );
            } else if (locationError.code === locationError.TIMEOUT) {
              onAccuracyUpdate?.(
                "Location is taking longer than expected. Waiting for a fresh reading...",
              );
            }
          },
          {
            enableHighAccuracy: true,
            timeout: LOCATION_TIMEOUT_MS,
            maximumAge: 0,
          },
        );
      } catch (locationStartError) {
        if (import.meta.env.DEV) {
          console.error(
            "[WFH] Unable to start employee location tracking",
            locationStartError,
          );
        }
        finish(
          reject,
          new Error(
            "Unable to start location detection. Check Windows Location Services and your browser's location permission, then try again.",
          ),
        );
      }
    });

  const registerCurrentLocation = async () => {
    if (workingRef.current) return;
    workingRef.current = true;
    setWorking(true); setError(""); setNotice("");
    try {
      setWorkingMessage("Getting a fresh GPS location for registration...");
      const registerAccuracyLimit = settings?.max_location_accuracy_meters || DEFAULT_MAX_WFH_LOCATION_ACCURACY_METERS;
      const location = await detectLocation((message) => setWorkingMessage(message), registerAccuracyLimit);
      setWorkingMessage("Saving your registered WFH location...");
      const { result } = await sendWithFreshLocation(
        (reading) => apiRequest("/wfh/location/register", {
          method: "POST",
          body: JSON.stringify(locationFields(reading)),
        }),
        location,
        registerAccuracyLimit,
      );
      setSettings((current) => current ? {
        ...current,
        home_location_configured: true,
        location_registration_required: false,
        home_location_accuracy_meters: result.accuracy_meters,
        home_location_updated_at: result.registered_at,
      } : current);
      setNotice(`Your WFH location is registered successfully (GPS accuracy ±${Math.round(result.accuracy_meters)} m). HR Admin can now view the registration status.`);
    } catch (e) {
      setError(e.message || "Unable to register your current location.");
    } finally {
      workingRef.current = false;
      if (mountedRef.current) { setWorking(false); setWorkingMessage(""); }
    }
  };

  const captureCameraFrames = async (actions) => {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error(
        "This browser does not support camera access. Use a current browser over HTTPS.",
      );
    }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: {
          facingMode: "user",
          width: { ideal: 640 },
          height: { ideal: 480 },
        },
      });
    } catch (cameraError) {
      if (cameraError.name === "NotFoundError") {
        throw new Error("No camera was detected. Connect or enable a camera and try again.");
      }
      if (cameraError.name === "NotReadableError") {
        throw new Error("The camera is already in use. Close other camera apps and try again.");
      }
      throw new Error("Camera permission is required for face verification. Allow camera access and try again.");
    }
    streamRef.current = stream;
    setCameraReady(true);
    const video = videoRef.current;
    try {
      if (!video) {
        throw new Error("Unable to start the camera. Please try again.");
      }
      video.srcObject = stream;
      await video.play();
      for (let attempt = 0; video.videoWidth === 0 && attempt < 30; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 100));
      }
      if (!video.videoWidth || !video.videoHeight) {
        throw new Error("Unable to read the camera. Please try again.");
      }

      const canvas = document.createElement("canvas");
      const context = canvas.getContext("2d");
      if (!context) {
        throw new Error("Unable to capture a camera frame. Please try again.");
      }
      const frames = [];
      for (let index = 0; index < 12; index += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 450));
        const scale = Math.min(
          1,
          640 / video.videoWidth,
          480 / video.videoHeight,
        );
        canvas.width = Math.max(1, Math.round(video.videoWidth * scale));
        canvas.height = Math.max(1, Math.round(video.videoHeight * scale));
        context.drawImage(video, 0, 0, canvas.width, canvas.height);
        frames.push(canvas.toDataURL("image/jpeg", 0.7));
      }
      if (frames.length !== 12 || !actions.length) {
        throw new Error("Camera verification did not complete. Please try again.");
      }
      return frames;
    } finally {
      stream.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
      if (video) video.srcObject = null;
      setCameraReady(false);
    }
  };

  const getLiveFrames = async (purpose, location) => {
    setWorkingMessage("Preparing secure face verification...");
    const challenge = await apiRequest("/wfh/face/challenge", {
      method: "POST",
      body: JSON.stringify({
        purpose,
        ...(location
          ? locationFields(location)
          : {}),
      }),
    });
    const actionText = challenge.actions
      .map((action) => {
        if (action === "blink") return "blink naturally";
        return action === "turn_left"
          ? "turn your head to your left, then face the camera"
          : "turn your head to your right, then face the camera";
      })
      .join(", then ");
    setWorkingMessage(`Camera check: ${actionText}. Keep your face in view.`);
    const frames = await captureCameraFrames(challenge.actions);
    return { challenge_id: challenge.challenge_id, frames };
  };

  const registerFace = async () => {
    setWorking(true);
    setError("");
    setNotice("");
    try {
      const capture = await getLiveFrames("register");
      await apiRequest("/wfh/face/register", {
        method: "POST",
        body: JSON.stringify(capture),
      });
      setNotice("Face registration completed securely.");
      setFallbackRequestPurpose(null);
      setShowFallbackOptions(false);
      if (!fallbackPurpose) await loadSettings();
    } catch (requestError) {
      setFallbackRequestPurpose("register");
      setShowFallbackOptions(false);
      setError(requestError.message || "Face registration failed. Please try again.");
    } finally {
      setWorking(false);
      setWorkingMessage("");
    }
  };

  const markAttendance = async (action) => {
    if (workingRef.current) return;
    workingRef.current = true;
    setWorking(true);
    setError("");
    setNotice("");
    setFallbackRequestPurpose(null);
    setShowFallbackOptions(false);
    setFallbackEmailSent(false);
    setLocationResult(null);
    try {
      setWorkingMessage("Checking today's attendance status...");
      const currentSettings = await apiRequest(
        `/wfh/me?purpose=${encodeURIComponent(action)}`,
      );
      if (!mountedRef.current) return;
      setSettings(currentSettings);

      setWorkingMessage("Checking your location...");
      const initialLocation = await detectLocation(
        setWorkingMessage,
        currentSettings.max_location_accuracy_meters,
      );
      setWorkingMessage("Verifying your location against approved WFH places...");
      const verified = await sendWithFreshLocation(
        (reading) => apiRequest("/wfh/location/verify", {
          method: "POST",
          body: JSON.stringify({
            ...locationFields(reading),
            purpose: action,
          }),
        }),
        initialLocation,
        currentSettings.max_location_accuracy_meters,
      );
      const locationVerification = verified.result;
      const location = verified.location;
      if (!mountedRef.current) return;
      setLocationResult({
        distance_meters: locationVerification.distance_meters,
        allowed_radius: locationVerification.allowed_radius,
        approved_location_name: locationVerification.approved_location_name || "Approved location",
      });

      setWorkingMessage("Starting face verification...");
      const capture = await getLiveFrames(action, location);
      if (!mountedRef.current) return;
      // Camera verification can take a while. The server only accepts a recent
      // location reading, so take a new live reading (still fully verified
      // server-side) when the earlier one is getting old.
      let finalLocation = location;
      if (performance.now() - location.receivedPerf > LOCATION_REUSE_LIMIT_MS) {
        setWorkingMessage("Refreshing your location for final verification...");
        finalLocation = await detectLocation(
          setWorkingMessage,
          currentSettings.max_location_accuracy_meters,
        );
        if (!mountedRef.current) return;
      }
      setWorkingMessage("Verifying your face...");
      const { result: record } = await sendWithFreshLocation(
        (reading) => apiRequest(`/wfh/${action.replace("_", "-")}`, {
          method: "POST",
          body: JSON.stringify({
            ...locationFields(reading),
            ...capture,
          }),
        }),
        finalLocation,
        currentSettings.max_location_accuracy_meters,
      );
      if (!mountedRef.current) return;
      const distance =
        action === "check_in"
          ? record.check_in_distance
          : record.check_out_distance;
      setLocationResult((current) => ({
        ...current,
        distance_meters: distance,
      }));
      setSettings((current) => ({
        ...current,
        attendance: record,
        attendance_already_marked: true,
      }));
      setNotice(
        `${action === "check_in" ? "WFH Check-In" : "WFH Check-Out"} recorded successfully at ${locationVerification.approved_location_name || "your approved location"}.`,
      );
    } catch (requestError) {
      if (mountedRef.current) {
        setFallbackRequestPurpose(action);
        setShowFallbackOptions(false);
        setError(requestError.message || "Unable to mark WFH attendance.");
        // A late attempt may have raised a manager approval request; show its status.
        if (action === "check_in") loadSettings();
      }
    } finally {
      workingRef.current = false;
      if (mountedRef.current) {
        setWorking(false);
        setWorkingMessage("");
      }
    }
  };

  const updateWfhBreak = async (action) => {
    if (workingRef.current) return;
    workingRef.current = true;
    setWorking(true);
    setError("");
    setNotice("");
    try {
      const result = await apiRequest(`/wfh/break/${action}`, { method: "POST" });
      setSettings((current) => current ? {
        ...current,
        attendance: result.attendance,
        wfh_breaks: result.wfh_breaks || [],
      } : current);
      setNotice(action === "start"
        ? "Your WFH break has started. Remember to end the break before checking out."
        : "Your WFH break has ended. You can resume work.");
    } catch (requestError) {
      setError(requestError.message || `Unable to ${action} your WFH break.`);
    } finally {
      workingRef.current = false;
      if (mountedRef.current) setWorking(false);
    }
  };

  const requestQrFallback = async () => {
    if (!fallbackRequestPurpose || fallbackPurpose) return;
    setWorking(true);
    setError("");
    setNotice("");
    setQrVerificationUrl("");
    try {
      const result = await apiRequest("/wfh/qr-fallback/request", {
        method: "POST",
        body: JSON.stringify({ purpose: fallbackRequestPurpose }),
      });
      const url = new URL(`${PUBLIC_FRONTEND_URL}/wfh-email-verification`);
      url.searchParams.set("token", result.token);
      setQrExpiresInMinutes(result.expires_in_minutes || 5);
      setQrVerificationUrl(url.toString());
      setNotice("A temporary QR code is ready. Scan it with the phone you will use for WFH verification.");
    } catch (requestError) {
      setError(requestError.message || "Unable to create the WFH QR verification code.");
    } finally {
      setWorking(false);
    }
  };

  const requestEmailFallback = async () => {
    if (!fallbackRequestPurpose || fallbackPurpose) return;
    setWorking(true);
    setError("");
    setNotice("");
    try {
      const result = await apiRequest("/wfh/email-fallback/request", {
        method: "POST",
        body: JSON.stringify({ purpose: fallbackRequestPurpose }),
      });
      setFallbackEmailSent(true);
      setNotice(`A secure verification link was sent to ${result.masked_email}. Open it on the device you will use for face and live-location verification. The link expires in ${result.expires_in_minutes} minutes.`);
    } catch (requestError) {
      setError(requestError.message || "Unable to send the WFH verification email.");
    } finally {
      setWorking(false);
    }
  };

  if (loading) {
    return (
      <div className="container-fluid py-4">
        <div className="text-center text-muted">Loading WFH attendance...</div>
      </div>
    );
  }

  const attendance = settings?.attendance;
  const breaks = attendance?.wfh_breaks || settings?.wfh_breaks || [];
  const activeBreak = breaks.find((item) => !item.ended_at);
  const totalBreakMinutes = breaks.reduce(
    (total, item) => total + (Number(item.duration_minutes) || 0),
    0,
  );
  const canStartBreak = Boolean(attendance?.check_in_time && !attendance?.check_out_time && !activeBreak);
  const canEndBreak = Boolean(attendance?.check_in_time && !attendance?.check_out_time && activeBreak);
  const latePermission = settings?.late_check_in_permission;
  // Outside the normal window, Check In stays available only to raise the first
  // approval request or while an approved extension is still valid.
  const lateCheckInBlocked = Boolean(
    latePermission?.is_late &&
      (latePermission.is_expired ||
        (latePermission.status && latePermission.status !== "approved")),
  );
  const canCheckIn =
    settings?.is_wfh_enabled &&
    settings?.face_registered &&
    !settings?.attendance_already_marked &&
    !lateCheckInBlocked;
  const canCheckOut =
    settings?.is_wfh_enabled &&
    settings?.face_registered &&
    attendance?.check_in_time &&
    !attendance?.check_out_time &&
    !activeBreak;

  return (
    <div className="container-fluid py-2">
      <div className="d-flex flex-wrap justify-content-between align-items-start gap-2 mb-4">
        <div>
          <h2 className="mb-1">Work From Home Attendance</h2>
          <p className="text-muted mb-0">
            Verify your live location from a GPS-capable mobile phone or another device with accurate location services, plus live face verification. Attendance is allowed only at your home or another HR-approved WFH location.
          </p>
        </div>
        <button
          type="button"
          className="btn btn-outline-secondary"
          onClick={() => {
            if (loadInFlightRef.current) return;
            setLoading(true);
            setError("");
            setNotice("");
            setStatusError(null);
            loadSettings();
          }}
          disabled={working || loading}
        >
          Refresh status
        </button>
      </div>

      {settings?.wfh_permitted_from && settings?.wfh_permitted_to && (
        <div className="alert alert-info py-2" role="status">
          WFH permission: <strong>{settings.wfh_permitted_from}</strong> to <strong>{settings.wfh_permitted_to}</strong>
          {settings.wfh_permitted_start_time && <> · Starts {settings.wfh_permitted_start_time}</>}
          {settings.wfh_permitted_end_time && <> · Ends {settings.wfh_permitted_end_time}</>}
        </div>
      )}
      {latePermission?.is_late && !settings?.attendance_already_marked && (
        latePermission.status === "approved" && !latePermission.is_expired ? (
          <div className="alert alert-success py-2" role="status">
            Late check-in approved by your reporting manager. You can check in until <strong>{formatTime(latePermission.extended_deadline)}</strong>.
          </div>
        ) : latePermission.status === "approved" ? (
          <div className="alert alert-danger py-2" role="status">
            Your approved late check-in permission expired at <strong>{formatTime(latePermission.extended_deadline)}</strong>. Check-in is no longer available today.
          </div>
        ) : latePermission.status === "pending" ? (
          <div className="alert alert-warning py-2" role="status">
            Late check-in request submitted to your reporting manager and awaiting approval. Check-in is disabled until it is approved
            {latePermission.is_expired ? "; the late check-in period has now ended." : <>, and will then be available until <strong>{formatTime(latePermission.extended_deadline)}</strong>.</>}
          </div>
        ) : latePermission.status === "rejected" ? (
          <div className="alert alert-danger py-2" role="status">
            Your reporting manager rejected late check-in permission for today. Check-in is not available outside the normal window (closed at {formatTime(latePermission.normal_deadline)}).
            {latePermission.decision_remarks && <div className="small mt-1">Remarks: {latePermission.decision_remarks}</div>}
          </div>
        ) : latePermission.is_expired ? (
          <div className="alert alert-danger py-2" role="status">
            The WFH check-in window closed at {formatTime(latePermission.normal_deadline)} and the late check-in permission period ended at {formatTime(latePermission.extended_deadline)}. Please contact HR Admin.
          </div>
        ) : (
          <div className="alert alert-warning py-2" role="status">
            The normal WFH check-in window closed at <strong>{formatTime(latePermission.normal_deadline)}</strong>. Late check-in requires reporting-manager approval. Select Check In to send an approval request to your reporting manager.
          </div>
        )
      )}
      {error && <div className="alert alert-danger" role="alert">{error}</div>}
      {statusError && settings && <div className="alert alert-danger" role="alert">{statusError.message}</div>}
      {notice && <div className="alert alert-success" role="status">{notice}</div>}
      {fallbackRequestPurpose && !fallbackPurpose && !(fallbackRequestPurpose === "check_in" && lateCheckInBlocked) && (
        <div className="alert alert-warning" role="region" aria-label="Alternative WFH verification options">
          <div className="d-flex flex-column flex-lg-row align-items-lg-start justify-content-between gap-3">
            <div>
              <div className="fw-semibold">Need another way?</div>
              <div className="small mt-1">
                Face and live-location verification are the primary method. If this device cannot access its camera or provide an accurate live location, you can continue on another supported device.
              </div>
            </div>
            <button
              type="button"
              className="btn btn-outline-primary flex-shrink-0"
              onClick={() => setShowFallbackOptions((visible) => !visible)}
              aria-expanded={showFallbackOptions}
              disabled={working}
            >
              {showFallbackOptions ? "Hide options" : "Try another way"}
            </button>
          </div>
          {showFallbackOptions && (
            <div className="mt-3 pt-3 border-top">
              <div className="d-flex flex-wrap gap-2">
                <button type="button" className="btn btn-outline-primary" onClick={requestEmailFallback} disabled={working}>
                  {working ? "Please wait..." : fallbackEmailSent ? "Send another email link" : "Send secure email link"}
                </button>
                <button type="button" className="btn btn-primary" onClick={requestQrFallback} disabled={working}>
                  {working ? "Please wait..." : "Use QR code"}
                </button>
              </div>
              {fallbackEmailSent && <div className="small mt-2">The email link was sent. You can also create a QR code for immediate access.</div>}
              <div className="small text-muted mt-2">QR and email are alternatives for opening the workflow on another device; they do not bypass the required face or live-location checks.</div>
            </div>
          )}
        </div>
      )}

      {qrVerificationUrl && (
        <div className="card border-primary shadow-sm mb-4" role="region" aria-label="QR mobile verification">
          <div className="card-body d-flex flex-column flex-md-row align-items-center gap-3 gap-md-4">
            <div
              className="bg-white p-2 p-sm-3 border rounded flex-shrink-0"
              style={{ width: "min(100%, 260px)", maxWidth: "100%" }}
            >
              <QRCodeSVG
                value={qrVerificationUrl}
                size={220}
                level="H"
                includeMargin
                style={{ display: "block", width: "100%", height: "auto", maxWidth: "220px", margin: "0 auto" }}
              />
            </div>
            <div className="flex-grow-1 w-100" style={{ minWidth: 0 }}>
              <h3 className="h5">Continue WFH verification on your phone</h3>
              <p className="mb-2">Scan this code with your phone camera. The code expires in {qrExpiresInMinutes} minutes and can be used only once.</p>
              <p className="small text-muted">The phone must be able to reach this HRMS frontend and backend. Camera and location access require a secure context (HTTPS, or localhost on the same device). A QR code does not bypass location or face verification.</p>
              <button type="button" className="btn btn-outline-secondary" onClick={() => setQrVerificationUrl("")}>Close QR code</button>
            </div>
          </div>
        </div>
      )}

      {statusError && !settings ? (
        <div className="card border-0 shadow-sm" role="alert">
          <div className="card-body">
            <h3 className="h5">
              {statusError.type === "unauthorized"
                ? NOT_AUTHORIZED_MESSAGE
                : statusError.type === "network"
                  ? CONNECTION_ERROR_MESSAGE
                  : "Unable to load WFH attendance."}
            </h3>
            <p className="text-muted mb-0">
              {statusError.type === "unauthorized"
                ? "WFH attendance is not available for this account role. Contact HR Admin if you need access."
                : statusError.type === "network"
                  ? "Check that the HRMS backend is running and reachable, then select Refresh status."
                  : statusError.message}
            </p>
          </div>
        </div>
      ) : !settings?.is_wfh_enabled || settings?.wfh_company_enabled === false ? (
        <div className="card border-0 shadow-sm">
          <div className="card-body">
            <h3 className="h5">{WFH_DISABLED_MESSAGE}</h3>
            <p className="text-muted mb-0">
              Use the existing HR attendance process. Contact HR Admin if you
              need WFH attendance access.
            </p>
          </div>
        </div>
      ) : !settings?.home_location_configured && !(settings?.approved_locations || []).length ? (
        <div className="card border-0 shadow-sm mb-4">
          <div className="card-body">
            <h3 className="h5">Register Your WFH Location</h3>
            <p className="text-muted">Use this phone or device to register the current location. Allow browser location access and ensure GPS accuracy is within the permitted limit. HR Admin can view the registered coordinates and reset this registration when a new location is needed.</p>
            {settings?.wfh_permitted_from && settings?.wfh_permitted_to && <p className="small">WFH permitted: <strong>{settings.wfh_permitted_from}</strong> to <strong>{settings.wfh_permitted_to}</strong>{settings.wfh_permitted_start_time && ` · ${settings.wfh_permitted_start_time}`}{settings.wfh_permitted_end_time && `–${settings.wfh_permitted_end_time}`}</p>}
            <button type="button" className="btn btn-primary" onClick={registerCurrentLocation} disabled={working}>{working ? "Registering location..." : "Use Current Location / Register Location"}</button>
            {working && <div className="alert alert-info py-2 mt-3 mb-0" role="status">{workingMessage}</div>}
          </div>
        </div>
      ) : (
        <>
          {settings?.location_registration_required && <div className="alert alert-warning" role="alert">HR Admin has reset your location. Register your current location again before WFH attendance.</div>}
          {settings?.home_location_configured && <div className="alert alert-success py-2" role="status">WFH location registered{settings.home_location_accuracy_meters != null ? ` · GPS accuracy ±${Math.round(settings.home_location_accuracy_meters)} m` : ""}{settings.home_location_updated_at ? ` · Registered ${formatIST(settings.home_location_updated_at)}` : ""}. Contact HR Admin if you need to register a different location.</div>}
          <div className="card border-0 shadow-sm mb-4">
            <div className="card-body">
              <h3 className="h5 mb-3">Today&apos;s Status</h3>
              <div className="row g-3">
                <div className="col-12 col-sm-6">
                  <div className="border rounded p-3 h-100">
                    <div className="small text-muted">Check-In</div>
                    <div className="fw-semibold">
                      {formatTime(attendance?.check_in_time)}
                    </div>
                  </div>
                </div>
                <div className="col-12 col-sm-6">
                  <div className="border rounded p-3 h-100">
                    <div className="small text-muted">Check-Out</div>
                    <div className="fw-semibold">
                      {formatTime(attendance?.check_out_time)}
                    </div>
                  </div>
                </div>
              </div>

              {!settings.face_registered && (!fallbackPurpose || fallbackPurpose === "register") ? (
                <div className="mt-4 p-3 bg-light rounded">
                  <h4 className="h6">Face Registration</h4>
                  <p className="small text-muted">
                    Register once using the live camera prompt. Only an
                    encrypted face template is retained; captured images are
                    discarded after verification.
                  </p>
                  <button
                    type="button"
                    className="btn btn-primary"
                    onClick={registerFace}
                    disabled={working}
                  >
                    Register face
                  </button>
                </div>
              ) : (
                <div className="mt-4">
                  <div className="small text-success fw-semibold mb-2">
                    <i className="bi bi-check-circle-fill me-2" aria-hidden="true"></i>
                    Face registration active
                  </div>
                  <div className="small text-muted">
                    Approved location geofencing is active. Home radius: {settings.allowed_radius} meters.
                  </div>
                  {(settings.approved_locations || []).length > 0 && (
                    <div className="small text-muted mt-1">
                      Additional approved places: {settings.approved_locations.map((location) => `${location.name} (${location.allowed_radius} m)`).join(", ")}
                    </div>
                  )}
                </div>
              )}

              {working && (
                <div className="mt-3 alert alert-info py-2" role="status">
                  {workingMessage}
                </div>
              )}

              <div
                className="mt-3 wfh-camera-frame"
                style={{ display: cameraReady ? "block" : "none" }}
              >
                <video
                  ref={videoRef}
                  className="wfh-camera-video"
                  autoPlay
                  muted
                  playsInline
                  aria-label="Live face verification camera"
                />
              </div>

              {locationResult && (
                <div className="mt-3 small">
                  <div className="text-success fw-semibold">
                    <i className="bi bi-geo-alt-fill me-2" aria-hidden="true"></i>
                    {locationResult.approved_location_name || "Approved location"} · {locationResult.distance_meters} meters away
                  </div>
                  <div className="text-muted">
                    Within allowed {locationResult.allowed_radius} metre radius
                  </div>
                </div>
              )}

              {attendance?.check_in_time && (
                <div className="mt-4 p-3 border rounded" aria-label="WFH break management">
                  <div className="d-flex flex-wrap justify-content-between align-items-center gap-2 mb-2">
                    <div>
                      <h4 className="h6 mb-1">Break Management</h4>
                      <div className="small text-muted">
                        Total completed break time: {Math.floor(totalBreakMinutes / 60)}h {String(totalBreakMinutes % 60).padStart(2, "0")}m
                      </div>
                    </div>
                    {!attendance?.check_out_time && canStartBreak && (
                      <button type="button" className="btn btn-outline-primary" onClick={() => updateWfhBreak("start")} disabled={working}>
                        Start Break
                      </button>
                    )}
                    {!attendance?.check_out_time && canEndBreak && (
                      <button type="button" className="btn btn-primary" onClick={() => updateWfhBreak("end")} disabled={working}>
                        End Break
                      </button>
                    )}
                  </div>
                  {activeBreak && <div className="alert alert-info py-2 mb-2">Break in progress since {activeBreak.start_time ? formatTime(activeBreak.start_time) : "the recorded start time"}. End the break before checking out.</div>}
                  {breaks.length > 0 ? (
                    <div className="table-responsive">
                      <table className="table table-sm mb-0">
                        <thead><tr><th>Break</th><th>Started</th><th>Ended</th><th>Duration</th></tr></thead>
                        <tbody>
                          {breaks.map((item, index) => (
                            <tr key={item.started_at || index}>
                              <td>{index + 1}</td>
                              <td>{item.start_time ? formatTime(item.start_time) : "—"}</td>
                              <td>{item.end_time ? formatTime(item.end_time) : "In progress"}</td>
                              <td>{item.duration_minutes == null ? "—" : `${item.duration_minutes} min`}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : <div className="small text-muted">No breaks recorded today.</div>}
                </div>
              )}

              <div className="mt-4 d-flex flex-wrap gap-2">
                {canCheckIn && (!fallbackPurpose || fallbackPurpose === "check_in") && (
                  <button
                    type="button"
                    className="btn btn-primary btn-lg"
                    onClick={() => markAttendance("check_in")}
                    disabled={working}
                  >
                    Check In
                  </button>
                )}
                {canCheckOut && (!fallbackPurpose || fallbackPurpose === "check_out") && (
                  <button
                    type="button"
                    className="btn btn-primary btn-lg"
                    onClick={() => markAttendance("check_out")}
                    disabled={working}
                  >
                    Check Out
                  </button>
                )}
                {settings.face_registered &&
                  settings.attendance_already_marked &&
                  !attendance && (
                    <div className="alert alert-warning mb-0">
                      Attendance is already marked for today. Contact HR Admin
                      if you believe this is incorrect.
                    </div>
                  )}
                {attendance?.check_out_time && (
                  <div className="alert alert-success mb-0">
                    Today&apos;s WFH attendance is complete.
                  </div>
                )}
              </div>
            </div>
          </div>
          <p className="small text-muted">
            Camera challenge checks for a blink and prompted head movement.
            Keep your whole face in view with bright light on your face. Move
            only when prompted, then face the camera again.
          </p>
        </>
      )}
    </div>
  );
}

export default WfhAttendance;
