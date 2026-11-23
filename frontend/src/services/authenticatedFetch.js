export async function authenticatedFetch(input, init = {}) {
  let session = null;
  try {
    session = JSON.parse(sessionStorage.getItem("hrms_session") || "null");
  } catch (error) {
    console.error("Unable to read HRMS session:", error);
  }
  const token =
    session?.token || session?.access_token || session?.session_token;
  const response = await fetch(input, {
    ...init,
    headers: {
      ...(init.headers || {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
  });

  if (response.status === 401) {
    window.dispatchEvent(new CustomEvent("hrms-authentication-expired"));
  }
  return response;
}
