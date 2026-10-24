
import { useEffect, useRef, useState } from "react";
import WfhAttendance from "./WfhAttendance";

const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL ||
  `${window.location.protocol}//${window.location.hostname}:8000`
).replace(/\/+$/, "");

function WfhEmailVerification() {
  const [state, setState] = useState({
    loading: true,
    error: "",
    token: "",
    purpose: "",
  });

  // The authenticated HRMS shell intentionally locks body scrolling and
  // scrolls inside .main-content. This public verification route is outside
  // that shell, so enable document scrolling while it is mounted.
  useEffect(() => {
    document.documentElement.classList.add("wfh-public-scroll");
    document.body.classList.add("wfh-public-scroll");

    return () => {
      document.documentElement.classList.remove("wfh-public-scroll");
      document.body.classList.remove("wfh-public-scroll");
    };
  }, []);

  // Prevent duplicate redemption when React StrictMode replays effects.
  const redemptionStarted = useRef(false);

  useEffect(() => {
    if (redemptionStarted.current) {
      return;
    }

    redemptionStarted.current = true;

    const query = new URLSearchParams(window.location.search);
    const rawToken = query.get("token");

    if (!rawToken || !rawToken.trim()) {
      setState({
        loading: false,
        error:
          "The verification token is missing. Please open the complete link from your latest HR One email.",
        token: "",
        purpose: "",
      });
      return;
    }

    // Remove the one-time token from the visible URL after reading it.
    window.history.replaceState(
      {},
      document.title,
      window.location.pathname
    );

    async function redeemLink() {
      try {
        const response = await fetch(
          `${API_BASE_URL}/api/attendance/wfh/email-fallback/redeem`,
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
            },
            body: JSON.stringify({ token: rawToken }),
          }
        );

        const data = await response.json().catch(() => ({}));

        if (!response.ok) {
          throw new Error(
            typeof data.detail === "string"
              ? data.detail
              : "This verification link is invalid or expired. Request a new link from HR One."
          );
        }

        if (
          !data.access_token ||
          !["register", "check_in", "check_out"].includes(data.purpose)
        ) {
          throw new Error(
            "The verification response was invalid. Request a new link from HR One."
          );
        }

        setState({
          loading: false,
          error: "",
          token: data.access_token,
          purpose: data.purpose,
        });
      } catch (error) {
        setState({
          loading: false,
          error:
            error.message ||
            "Unable to verify the link. Check your connection and request a new link if necessary.",
          token: "",
          purpose: "",
        });
      }
    }

    // Intentionally do not cancel this request in the effect cleanup:
    // React StrictMode can run setup -> cleanup -> setup in development.
    // The ref ensures the one-time token is redeemed only once per mount.
    void redeemLink();
  }, []);

  if (state.loading) {
    return (
      <div className="container-fluid px-2 px-sm-3 py-3 py-md-5 wfh-public-page">
        <div className="alert alert-info" role="status">
          Verifying your secure WFH link...
        </div>
      </div>
    );
  }

  if (state.error) {
    return (
      <div className="container-fluid px-2 px-sm-3 py-3 py-md-5 wfh-public-page">
        <div className="card shadow-sm border-0">
          <div className="card-body">
            <h1 className="h4">WFH email verification</h1>
            <div className="alert alert-danger mb-3" role="alert">
              {state.error}
            </div>
            <p className="text-muted mb-0">
              For security, verification links can expire or be used only once.
              Request a fresh link from your signed-in HR One account if needed.
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="wfh-public-page">
      <div className="container-fluid px-2 px-sm-3 pt-3 wfh-public-page">
        <div className="alert alert-info" role="status">
          Secure verification link opened. Complete the requested face and
          live-location checks below. This session is limited to this WFH
          action and expires shortly.
        </div>
      </div>

      <WfhAttendance
        session={{ token: state.token }}
        fallbackPurpose={state.purpose}
      />
    </div>
  );
}

export default WfhEmailVerification;
