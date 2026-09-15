/* ==========================================================
   KRAKEN - SECURE LOGOUT
   Client state purge + server session revoke, phir wapas /login.
   (Server bhi band karna ho to: export KRAKEN_LOGOUT_SHUTDOWN=1)
   ========================================================== */
(function () {
  "use strict";

  var CYAN = "#00f0ff";
  var RED = "#ffb4ab";
  var BG = "#0f141b";
  var MONO = "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace";
  var busy = false;

  function injectStyle() {
    if (document.getElementById("kraken-logout-style")) return;
    var css = ""
      + "#kraken-logout-veil{position:fixed;inset:0;z-index:2147483647;display:flex;"
      + "align-items:center;justify-content:center;background:" + BG + "f2;"
      + "backdrop-filter:blur(3px);font-family:" + MONO + ";padding:16px;}"
      + "#kraken-logout-veil .kl-box{width:100%;max-width:520px;border:1px solid " + CYAN + "59;"
      + "background:#090e16;box-shadow:0 0 40px rgba(0,240,255,.12);padding:22px 20px;}"
      + "#kraken-logout-veil .kl-tag{font-size:11px;letter-spacing:.22em;color:" + RED + ";margin-bottom:14px;}"
      + "#kraken-logout-veil h2{font-size:17px;line-height:1.45;color:" + CYAN + ";margin:0 0 10px;font-weight:700;letter-spacing:.04em;}"
      + "#kraken-logout-veil p{font-size:12.5px;line-height:1.6;color:#b9cacb;margin:0 0 18px;}"
      + "#kraken-logout-veil .kl-acts{display:flex;flex-wrap:wrap;gap:10px;}"
      + "#kraken-logout-veil button{font-family:" + MONO + ";font-size:12px;letter-spacing:.16em;"
      + "padding:10px 16px;background:transparent;cursor:pointer;transition:all .18s ease;}"
      + "#kraken-logout-veil .kl-go{border:1px solid " + RED + "80;color:" + RED + ";}"
      + "#kraken-logout-veil .kl-go:hover{background:" + RED + ";color:#690005;}"
      + "#kraken-logout-veil .kl-no{border:1px solid #3b494b;color:#849495;}"
      + "#kraken-logout-veil .kl-no:hover{border-color:" + CYAN + "80;color:" + CYAN + ";}"
      + "#kraken-logout-veil .kl-log{font-size:12px;line-height:1.85;color:#849495;"
      + "margin:0;white-space:pre-wrap;word-break:break-word;}"
      + "#kraken-logout-veil .kl-log b{color:" + CYAN + ";font-weight:500;}"
      + "#kraken-logout-veil .kl-log i{color:" + RED + ";font-style:normal;}"
      + "@keyframes kl-blink{50%{opacity:.25}}"
      + "#kraken-logout-veil .kl-cursor{animation:kl-blink 1s steps(1) infinite;color:" + CYAN + ";}";
    var el = document.createElement("style");
    el.id = "kraken-logout-style";
    el.textContent = css;
    document.head.appendChild(el);
  }

  function veil() {
    var v = document.getElementById("kraken-logout-veil");
    if (!v) {
      v = document.createElement("div");
      v.id = "kraken-logout-veil";
      v.setAttribute("role", "dialog");
      v.setAttribute("aria-modal", "true");
      document.body.appendChild(v);
    }
    return v;
  }

  /* ---------- step 1: confirmation (galti se demo band na ho) ---------- */
  function confirmPanel() {
    injectStyle();
    var v = veil();
    v.innerHTML = ""
      + '<div class="kl-box">'
      + '  <div class="kl-tag">// SECURE LOGOUT — CONFIRM</div>'
      + '  <h2>TERMINATE SESSION?</h2>'
      + '  <p>Your clearance will be revoked and the local intel cache purged. You will be'
      + '  returned to the secure access screen and must authenticate again to continue.</p>'
      + '  <div class="kl-acts">'
      + '    <button class="kl-go" id="kl-confirm" type="button">CONFIRM TERMINATION</button>'
      + '    <button class="kl-no" id="kl-abort" type="button">ABORT</button>'
      + '  </div>'
      + '</div>';

    var go = document.getElementById("kl-confirm");
    var no = document.getElementById("kl-abort");
    go.addEventListener("click", runTeardown);
    no.addEventListener("click", dismiss);
    document.addEventListener("keydown", onKey, true);
    go.focus();
  }

  function onKey(e) {
    if (busy) return;
    if (e.key === "Escape") { e.preventDefault(); dismiss(); }
    else if (e.key === "Enter") { e.preventDefault(); runTeardown(); }
  }

  function dismiss() {
    document.removeEventListener("keydown", onKey, true);
    var v = document.getElementById("kraken-logout-veil");
    if (v) v.remove();
  }

  /* ---------- step 2: client state purge ---------- */
  function purgeClientState() {
    try { localStorage.clear(); } catch (e) {}
    try { sessionStorage.clear(); } catch (e) {}
    try {
      document.cookie.split(";").forEach(function (c) {
        var name = c.split("=")[0].trim();
        if (!name) return;
        var dead = "=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
        document.cookie = name + dead + "; path=/";
        document.cookie = name + dead + "; path=" + location.pathname;
      });
    } catch (e) {}
    try {
      if (window.caches && caches.keys) {
        caches.keys().then(function (keys) { keys.forEach(function (k) { caches.delete(k); }); });
      }
    } catch (e) {}
    try {
      if (window.indexedDB && indexedDB.databases) {
        indexedDB.databases().then(function (dbs) {
          dbs.forEach(function (db) { if (db && db.name) indexedDB.deleteDatabase(db.name); });
        });
      }
    } catch (e) {}
  }

  /* ---------- step 3: teardown log + server kill + window close ---------- */
  function runTeardown() {
    if (busy) return;
    busy = true;
    document.removeEventListener("keydown", onKey, true);
    injectStyle();

    var v = veil();
    v.innerHTML = ""
      + '<div class="kl-box">'
      + '  <div class="kl-tag">// SECURE LOGOUT — IN PROGRESS</div>'
      + '  <p class="kl-log" id="kl-log"></p>'
      + '</div>';
    var log = document.getElementById("kl-log");

    function line(text, cls) {
      var tag = cls === "err" ? "i" : "b";
      log.innerHTML += "> <" + tag + ">" + text + "</" + tag + ">\n";
    }

    line("Revoking clearance token");
    purgeClientState();
    line("Local intel cache purged");

    // saare timers/pending work rok do taaki background me kuch chalta na rahe
    try {
      var hi = setTimeout(function () {}, 0);
      for (var i = 0; i <= hi; i++) { clearTimeout(i); clearInterval(i); }
    } catch (e) {}

    line("Revoking server-side clearance");

    var done = false;

    // Server ne kaha shutdown -> window band; warna /login par wapas.
    function finish(payload) {
      if (done) return;
      done = true;

      if (payload && payload.mode === "shutdown") {
        line("Kraken core OFFLINE");
        line("Closing terminal window");
        setTimeout(closeWindow, 450);
        return;
      }

      line("Clearance revoked");
      line("Returning to secure access");
      var target = (payload && payload.redirect) || "/login";
      setTimeout(function () { window.location.replace(target); }, 500);
    }

    var req;
    try {
      req = fetch("/api/logout", {
        method: "POST",
        cache: "no-store",
        keepalive: true,
        headers: { "Content-Type": "application/json" }
      });
    } catch (e) {
      req = Promise.reject(e);
    }
    req.then(function (res) {
      if (res && (res.status === 401 || res.status === 403)) {
        // Clearance pehle hi dead thi -> seedhe login par.
        sessionExpired();
        return;
      }
      res.json().then(function (data) { finish(data); }, function () { finish(null); });
    }, function () {
      // Core reachable nahi (ya KRAKEN_LOGOUT_SHUTDOWN mode me mar gaya).
      finish({ mode: "shutdown" });
    });
    setTimeout(function () { finish(null); }, 2500);
  }

  function sessionExpired() {
    var v = document.getElementById("kraken-logout-veil");
    if (v) {
      v.innerHTML = ""
        + '<div class="kl-box" style="text-align:center">'
        + '  <div class="kl-tag" style="letter-spacing:.3em">// CLEARANCE ALREADY REVOKED</div>'
        + '  <h2 style="margin-bottom:14px">SESSION EXPIRED</h2>'
        + '  <p style="margin:0">Local state purged. Returning to the secure access screen'
        + '  <span class="kl-cursor"> █</span></p>'
        + '</div>';
    }
    setTimeout(function () { window.location.replace("/login"); }, 900);
  }

  function closeWindow() {
    try { window.open("", "_self"); } catch (e) {}
    try { window.close(); } catch (e) {}
    // Browser sirf script-opened tabs hi close karne deta hai; warna manual note.
    setTimeout(function () {
      var v = document.getElementById("kraken-logout-veil");
      if (!v) return;
      v.innerHTML = ""
        + '<div class="kl-box" style="text-align:center">'
        + '  <div class="kl-tag" style="letter-spacing:.3em">// SESSION TERMINATED</div>'
        + '  <h2 style="margin-bottom:14px">KRAKEN CORE OFFLINE</h2>'
        + '  <p style="margin:0">Clearance revoked and the Kraken core is offline.<br>'
        + '  You may now close this tab &mdash; <span style="color:' + CYAN + '">⌘W</span> / '
        + '  <span style="color:' + CYAN + '">Ctrl+W</span>.<span class="kl-cursor"> █</span></p>'
        + '</div>';
    }, 600);
  }

  // Global entry point — templates ke SECURE LOGOUT button isi ko call karte hain.
  window.krakenLogout = function (evt) {
    if (evt && evt.preventDefault) evt.preventDefault();
    if (busy) return false;
    confirmPanel();
    return false;
  };
})();
