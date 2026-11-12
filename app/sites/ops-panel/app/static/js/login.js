/** Login page helpers (no inline script — CSP script-src 'self'). */
(function () {
  if (location.search.indexOf('error=1') !== -1) {
    var el = document.getElementById('login-error');
    if (el) el.hidden = false;
  }

  function isInteractive(target) {
    return target && target.closest('input, button, label, .login-submit, .login-error');
  }

  document.addEventListener('contextmenu', function (e) {
    if (!isInteractive(e.target)) e.preventDefault();
  });

  document.addEventListener('mousedown', function (e) {
    if (e.button !== 0) return;
    if (!isInteractive(e.target)) e.preventDefault();
  });

  document.addEventListener('selectstart', function (e) {
    if (!isInteractive(e.target)) e.preventDefault();
  });
})();
