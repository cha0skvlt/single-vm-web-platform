/** Apply stored theme before CSS paint (no inline script — CSP script-src 'self'). */
(function () {
  var light = false;
  try {
    light = localStorage.getItem('prod-theme') === 'light';
    if (light) document.documentElement.setAttribute('data-theme', 'light');
  } catch (e) {}
  var meta = document.querySelector('meta[name="theme-color"]');
  if (!meta) {
    meta = document.createElement('meta');
    meta.name = 'theme-color';
    document.head.appendChild(meta);
  }
  meta.content = light ? '#d4cebf' : '#0b0c0e';
})();
