/* ──────────────────────────────────────────────
   auth.js — shared auth helpers for all pages
────────────────────────────────────────────── */

/**
 * Call from protected pages. If not logged in, redirects to login.
 * Returns the username string on success.
 */
async function requireAuth() {
  try {
    const res = await fetch('/api/auth/me');
    if (!res.ok) {
      window.location.replace('/static/login.html');
      return null;
    }
    const data = await res.json();
    return data.username;
  } catch {
    window.location.replace('/static/login.html');
    return null;
  }
}

async function logout() {
  await fetch('/api/auth/logout', { method: 'POST' }).catch(() => {});
  window.location.replace('/static/login.html');
}
