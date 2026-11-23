// Session-aware entry point for public auth pages and the authenticated shell.

import { lazy, Suspense, useEffect, useState } from "react";

const MainLayout = lazy(() => import("./components/MainLayout"));
const Login = lazy(() => import("./pages/Login"));
const ActivateAccount = lazy(() => import("./pages/ActivateAccount"));

const ForgotPassword = lazy(() => import("./pages/ForgotPassword"));
const VerifyResetOTP = lazy(() => import("./pages/VerifyResetOTP"));
const ResetPassword = lazy(() => import("./pages/ResetPassword"));
const WfhEmailVerification = lazy(() => import("./pages/WfhEmailVerification"));

import { readStoredSession } from "./auth";


function AppContent() {
  const [session, setSession] = useState(readStoredSession);

  useEffect(() => {
    const handleAuthenticationExpired = () => {
      try {
        sessionStorage.removeItem("hrms_session");
      } catch {
        // Continue to the signed-out view even if storage is unavailable.
      }
      setSession(null);
    };
    window.addEventListener("hrms-authentication-expired", handleAuthenticationExpired);
    return () => window.removeEventListener("hrms-authentication-expired", handleAuthenticationExpired);
  }, []);

  const pathname = window.location.pathname;


  // ---------------------------------------------------------
  // Forgot Password
  // ---------------------------------------------------------
  if (
    pathname === "/forgot-password" ||
    pathname.endsWith("/forgot-password")
  ) {
    return <ForgotPassword />;
  }


  // ---------------------------------------------------------
  // Verify Reset OTP
  // ---------------------------------------------------------
  if (
    pathname === "/verify-reset-otp" ||
    pathname.endsWith("/verify-reset-otp")
  ) {
    return <VerifyResetOTP />;
  }


  // ---------------------------------------------------------
  // Reset Password
  // ---------------------------------------------------------
  if (
    pathname === "/reset-password" ||
    pathname.endsWith("/reset-password")
  ) {
    return <ResetPassword />;
  }


  // ---------------------------------------------------------
  // One-time WFH email verification fallback
  // ---------------------------------------------------------
  if (
    pathname === "/wfh-email-verification" ||
    pathname.endsWith("/wfh-email-verification")
  ) {
    return <WfhEmailVerification />;
  }


  // ---------------------------------------------------------
  // Activate Account
  // ---------------------------------------------------------
  if (
    pathname === "/activate" ||
    pathname.endsWith("/activate")
  ) {
    return <ActivateAccount />;
  }


  // ---------------------------------------------------------
  // Login
  // ---------------------------------------------------------
  if (
    !session &&
    (
      pathname === "/login" ||
      pathname.endsWith("/login")
    )
  ) {
    return <Login onLogin={setSession} />;
  }


  // ---------------------------------------------------------
  // Authenticated Application
  // ---------------------------------------------------------
  return session ? (
    <MainLayout
      session={session}
      onLogout={() => setSession(null)}
    />
  ) : (
    <Login onLogin={setSession} />
  );
}


function App() {
  return (
    <Suspense
      fallback={
        <div
          className="d-flex align-items-center justify-content-center"
          role="status"
          aria-live="polite"
          style={{ minHeight: "100vh" }}
        >
          <span className="spinner-border" aria-hidden="true" />
          <span className="visually-hidden">Loading application...</span>
        </div>
      }
    >
      <AppContent />
    </Suspense>
  );
}

export default App;