// Session-aware entry point for public auth pages and the authenticated shell.

import { useState } from "react";

import MainLayout from "./components/MainLayout";
import Login from "./pages/Login";
import ActivateAccount from "./pages/ActivateAccount";

import ForgotPassword from "./pages/ForgotPassword";
import VerifyResetOTP from "./pages/VerifyResetOTP";
import ResetPassword from "./pages/ResetPassword";
import WfhEmailVerification from "./pages/WfhEmailVerification";

import { readStoredSession } from "./auth";


function App() {
  const [session, setSession] = useState(readStoredSession);

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


export default App;