// Apply the stored theme before first paint so there is no flash of the wrong palette.
// A separate file, not inline in index.html (security review fix round 1): admin.opsgrids.com's
// Content-Security-Policy has no 'unsafe-inline' in script-src, so this must be served as its own
// same-origin resource — the CSP already covers `default-src 'self'`, which admits this file with
// no further exception.
try {
  var t = localStorage.getItem("opsgrids.operator.theme");
  document.documentElement.setAttribute("data-theme", t === "dark" ? "dark" : "light");
} catch (e) {
  document.documentElement.setAttribute("data-theme", "light");
}
