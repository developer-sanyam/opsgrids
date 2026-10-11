// Apply the stored theme before first paint so there is no flash of the wrong palette.
// Its own same-origin file, not inline in index.html: the site's Content-Security-Policy has
// no 'unsafe-inline' in script-src, so an inline snippet here would never run.
try {
  var t = localStorage.getItem("opsgrids.engage.theme");
  document.documentElement.setAttribute("data-theme", t === "dark" ? "dark" : "light");
} catch (e) {
  document.documentElement.setAttribute("data-theme", "light");
}
