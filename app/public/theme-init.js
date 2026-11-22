// Pre-paint theme: runs before the app bundle so a saved light theme never flashes dark.
// A separate file (not inline) because the app's CSP forbids inline scripts.
try {
  if (localStorage.getItem("vedit.theme") === "light") {
    document.documentElement.classList.add("light");
    document.querySelector('meta[name="color-scheme"]').setAttribute("content", "light");
  }
} catch (e) {}
