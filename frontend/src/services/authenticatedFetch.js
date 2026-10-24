export function authenticatedFetch(input, init = {}) {
  let session = null;
  try {
    session = JSON.parse(sessionStorage.getItem("hrms_session") || "null");
  } catch (error) {
    console.error("Unable to read HRMS session:", error);
  }
  const token =
    session?.token || session?.access_token || session?.session_token;
  return fetch(input, {
    ...init,
    headers: {
      ...(init.headers || {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
  });
}
