/* ==========================================================
   KRAKEN - HEADER CONTROLS (bell + settings)
   Dono pehle dead buttons the. Ab:
     BELL     -> asli alerts, seedha case database se (badge count ke saath)
     SETTINGS -> asli options jo graph par turant asar dalte hain
   Settings localStorage mein save hoti hain, isliye page badalne par bhi rehti hain.
   ========================================================== */
(function () {
  "use strict";

  var CYAN = "#00f0ff";
  var MONO = "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace";
  var STORE_KEY = "kraken.settings.v1";

  var DEFAULTS = {
    towerMin: 3,        // tower link tabhi dikhao jab itni baar ping hua ho
    minWeight: 1,       // is se kam evidence wale links chhupa do
    showDevices: true,  // phone / account nodes graph par
    minConfidence: 0    // is se kam confidence wali predictions chhupa do
  };

  // ---------- settings store ----------
  function readSettings() {
    var saved = {};
    try { saved = JSON.parse(localStorage.getItem(STORE_KEY)) || {}; } catch (e) { saved = {}; }
    var out = {};
    Object.keys(DEFAULTS).forEach(function (k) {
      out[k] = (saved[k] === undefined || saved[k] === null) ? DEFAULTS[k] : saved[k];
    });
    return out;
  }

  function writeSettings(next) {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(next)); } catch (e) {}
  }

  // Dusre scripts (network page) isi se query string banate hain
  window.krakenSettings = readSettings;
  window.krakenGraphQuery = function () {
    var s = readSettings();
    return "?tower_min=" + encodeURIComponent(s.towerMin) +
           "&min_weight=" + encodeURIComponent(s.minWeight) +
           "&devices=" + (s.showDevices ? "1" : "0");
  };

  // ---------- shared styles ----------
  function injectStyle() {
    if (document.getElementById("kraken-header-style")) return;
    var css = ""
      + ".kh-pop{position:absolute;top:56px;right:8px;width:340px;max-width:calc(100vw - 16px);"
      + "background:#0f141b;border:1px solid #3b494b;box-shadow:0 10px 40px rgba(0,0,0,.6);"
      + "z-index:60;font-family:" + MONO + ";max-height:70vh;overflow-y:auto;}"
      + ".kh-pop h4{margin:0;padding:10px 12px;font-size:10px;letter-spacing:.2em;color:" + CYAN + ";"
      + "border-bottom:1px solid #3b494b;background:#171c24;display:flex;justify-content:space-between;align-items:center;}"
      + ".kh-row{padding:10px 12px;border-bottom:1px solid rgba(59,73,75,.4);font-size:11px;color:#b9cacb;}"
      + ".kh-row.clickable{cursor:pointer;} .kh-row.clickable:hover{background:#171c24;}"
      + ".kh-row b{display:block;color:#fff;font-weight:600;margin-bottom:2px;font-size:11px;}"
      + ".kh-row small{color:#849495;font-size:9px;display:block;margin-top:3px;letter-spacing:.08em;}"
      + ".kh-tag{float:right;font-size:9px;padding:0 4px;border:1px solid;letter-spacing:.1em;}"
      + ".kh-set{padding:12px;border-bottom:1px solid rgba(59,73,75,.4);}"
      + ".kh-set label{display:flex;justify-content:space-between;align-items:center;font-size:10px;"
      + "color:#b9cacb;letter-spacing:.1em;margin-bottom:6px;}"
      + ".kh-set input[type=range]{width:100%;accent-color:" + CYAN + ";}"
      + ".kh-set .kh-val{color:" + CYAN + ";font-size:10px;}"
      + ".kh-switch{display:flex;justify-content:space-between;align-items:center;font-size:10px;"
      + "color:#b9cacb;letter-spacing:.1em;cursor:pointer;}"
      + ".kh-switch input{accent-color:" + CYAN + ";width:14px;height:14px;cursor:pointer;}"
      + ".kh-btn{width:100%;font-family:" + MONO + ";font-size:10px;letter-spacing:.14em;padding:8px;"
      + "background:transparent;border:1px solid #3b494b;color:#849495;cursor:pointer;transition:all .15s;}"
      + ".kh-btn:hover{border-color:" + CYAN + ";color:" + CYAN + ";}"
      + ".kh-btn.danger:hover{border-color:#ffb4ab;color:#ffb4ab;}"
      + ".kh-badge{position:absolute;top:2px;right:2px;min-width:14px;height:14px;line-height:14px;"
      + "border-radius:7px;background:#ffb4ab;color:#690005;font-size:9px;font-family:" + MONO + ";"
      + "text-align:center;padding:0 3px;font-weight:700;}"
      + ".kh-host{position:relative;}";
    var el = document.createElement("style");
    el.id = "kraken-header-style";
    el.textContent = css;
    document.head.appendChild(el);
  }

  // ---------- popover plumbing ----------
  var openPop = null;

  function closePop() {
    if (openPop && openPop.parentNode) openPop.parentNode.removeChild(openPop);
    openPop = null;
  }

  document.addEventListener("click", function (e) {
    if (openPop && !openPop.contains(e.target) && !e.target.closest(".kh-host")) closePop();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closePop();
  });

  function showPop(html) {
    closePop();
    injectStyle();
    var pop = document.createElement("div");
    pop.className = "kh-pop";
    pop.innerHTML = html;
    document.body.appendChild(pop);
    openPop = pop;
    return pop;
  }

  // ---------- BELL: asli alerts ----------
  function alertRow(title, detail, href, tone) {
    var color = tone === "high" ? "#ffb4ab" : (tone === "warn" ? "#feb700" : CYAN);
    return '<div class="kh-row clickable" onclick="window.location.href=\'' + href + '\'">'
      + '<b style="color:' + color + '">' + title + '</b>' + detail
      + '<small>' + href + '</small></div>';
  }

  function buildAlerts(st, preds, syn, manual, dossiers) {
    var rows = [];

    var highRisk = (dossiers || []).filter(function (d) { return d.score >= 90; });
    if (highRisk.length) {
      rows.push(alertRow(
        highRisk.length + " subjects at RIS 90+",
        "Highest: " + highRisk[0].name + " (" + highRisk[0].score + ", " + highRisk[0].badge + ")",
        "/profile?filter=flags", "high"));
    }

    var strong = (preds || []).filter(function (p) { return p.confidence >= 95; });
    if (strong.length) {
      rows.push(alertRow(
        strong.length + " AI links above 95%",
        "Strongest: " + preds[0].source + " ↔ " + preds[0].target
          + " (" + Number(preds[0].confidence).toFixed(1) + "%)",
        "/predictions", "warn"));
    }

    if (st.calls_unattributed) {
      rows.push(alertRow(
        st.calls_unattributed + " unattributed SIMs",
        st.calls_unattributed + " of " + st.calls_processed + " intercepted calls have no registered subscriber.",
        "/network", "warn"));
    }

    if ((syn.syndicates || []).length) {
      var big = syn.syndicates[0];
      rows.push(alertRow(
        (syn.syndicates || []).length + " syndicates mapped",
        "Largest: " + big.name + " — " + big.size + " members, cohesion " + big.cohesion + "%",
        "/syndicates", "info"));
    }

    var edits = manual && manual.state
      ? (manual.state.uploads || 0) + (manual.state.removals || 0) : 0;
    if (edits) {
      rows.push(alertRow(
        edits + " operator edits active",
        (manual.state.uploads || 0) + " upload(s), " + (manual.state.removals || 0) + " removal(s) applied to the live graph.",
        "/intake", "info"));
    }

    return rows;
  }

  async function openBell(btn) {
    var pop = showPop('<h4>ALERTS<span style="color:#849495">LIVE CASE DATA</span></h4>'
      + '<div class="kh-row" style="color:#849495">Reading case database...</div>');
    try {
      var res = await Promise.all([
        fetch("/api/stats"), fetch("/api/graph-data"),
        fetch("/api/syndicates"), fetch("/api/manual-log"), fetch("/api/dossiers")
      ]);
      var st = (await res[0].json()).stats || {};
      var g = await res[1].json();
      var syn = await res[2].json();
      var manual = await res[3].json();
      var dossiers = (await res[4].json()).targets || [];

      var rows = buildAlerts(st, g.predictions || [], syn, manual, dossiers);
      pop.innerHTML = '<h4>ALERTS<span style="color:#849495">' + rows.length + ' ACTIVE</span></h4>'
        + (rows.length ? rows.join("")
           : '<div class="kh-row" style="color:#849495">No active alerts. Case graph nominal.</div>');
      setBadge(btn, rows.length);
    } catch (e) {
      pop.innerHTML = '<h4>ALERTS</h4><div class="kh-row" style="color:#ffb4ab">Case database unreachable.</div>';
    }
  }

  function setBadge(btn, count) {
    var existing = btn.querySelector(".kh-badge");
    if (existing) existing.remove();
    if (!count) return;
    var badge = document.createElement("span");
    badge.className = "kh-badge";
    badge.innerText = count > 9 ? "9+" : String(count);
    btn.appendChild(badge);
  }

  // ---------- SETTINGS: asli options ----------
  function openSettings() {
    var s = readSettings();
    var pop = showPop(
      '<h4>SETTINGS<span style="color:#849495">SAVED LOCALLY</span></h4>'

      + '<div class="kh-set">'
      + '  <label>TOWER LINK THRESHOLD <span class="kh-val" id="kh-tower-val">' + s.towerMin + '+ pings</span></label>'
      + '  <input type="range" id="kh-tower" min="1" max="8" step="1" value="' + s.towerMin + '">'
      + '  <div style="font-size:9px;color:#849495;margin-top:4px">Most subjects ping most towers. A higher value keeps only repeated co-location.</div>'
      + '</div>'

      + '<div class="kh-set">'
      + '  <label>MIN EVIDENCE PER LINK <span class="kh-val" id="kh-weight-val">' + s.minWeight + '</span></label>'
      + '  <input type="range" id="kh-weight" min="1" max="5" step="1" value="' + s.minWeight + '">'
      + '  <div style="font-size:9px;color:#849495;margin-top:4px">Hides single-contact links so only repeated contact is shown.</div>'
      + '</div>'

      + '<div class="kh-set">'
      + '  <label>MIN AI CONFIDENCE <span class="kh-val" id="kh-conf-val">' + s.minConfidence + '%</span></label>'
      + '  <input type="range" id="kh-conf" min="0" max="99" step="1" value="' + s.minConfidence + '">'
      + '</div>'

      + '<div class="kh-set">'
      + '  <label class="kh-switch" for="kh-devices">SHOW SIM / ACCOUNT NODES'
      + '    <input type="checkbox" id="kh-devices"' + (s.showDevices ? " checked" : "") + '></label>'
      + '</div>'

      + '<div class="kh-set" id="kh-source" style="font-size:9px;color:#849495;letter-spacing:.08em">Reading source...</div>'

      + '<div class="kh-set" style="display:flex;flex-direction:column;gap:8px;border-bottom:none">'
      + '  <button class="kh-btn" id="kh-apply">APPLY &amp; RELOAD</button>'
      + '  <button class="kh-btn" id="kh-defaults">RESTORE DEFAULTS</button>'
      + '  <button class="kh-btn danger" id="kh-reset">RESET GRAPH TO SOURCE DATA</button>'
      + '</div>');

    var bind = function (id, valId, suffix) {
      var input = document.getElementById(id);
      input.addEventListener("input", function () {
        document.getElementById(valId).innerText = input.value + (suffix || "");
      });
    };
    bind("kh-tower", "kh-tower-val", "+ pings");
    bind("kh-weight", "kh-weight-val", "");
    bind("kh-conf", "kh-conf-val", "%");

    fetch("/api/stats").then(function (r) { return r.json(); }).then(function (d) {
      var st = d.stats || {};
      var el = document.getElementById("kh-source");
      if (el) {
        el.innerHTML = "SOURCE: " + (st.source || ["unknown"])[0]
          + "<br>" + st.persons + " persons &middot; " + st.confirmed_links + " confirmed links";
      }
    }).catch(function () {});

    document.getElementById("kh-apply").addEventListener("click", function () {
      writeSettings({
        towerMin: Number(document.getElementById("kh-tower").value),
        minWeight: Number(document.getElementById("kh-weight").value),
        minConfidence: Number(document.getElementById("kh-conf").value),
        showDevices: document.getElementById("kh-devices").checked
      });
      closePop();
      window.location.reload();
    });

    document.getElementById("kh-defaults").addEventListener("click", function () {
      writeSettings(DEFAULTS);
      closePop();
      window.location.reload();
    });

    document.getElementById("kh-reset").addEventListener("click", function () {
      var btn = document.getElementById("kh-reset");
      if (btn.dataset.armed !== "1") {
        btn.dataset.armed = "1";
        btn.innerText = "CONFIRM — THIS CLEARS ALL MANUAL EDITS";
        return;
      }
      fetch("/api/reset-graph", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}"
      }).then(function () { window.location.reload(); });
    });
  }

  // ---------- header buttons ko dhoondo aur wire karo ----------
  function wire() {
    injectStyle();
    // Kuch pages ka top bar <header> hai, network ka <nav> -- dono dekho
    var icons = document.querySelectorAll("header .material-symbols-outlined, nav.fixed.top-0 .material-symbols-outlined");
    icons.forEach(function (icon) {
      var name = (icon.textContent || "").trim();
      var btn = icon.closest("button");
      if (!btn || btn.dataset.krakenWired) return;

      if (name === "notifications") {
        btn.dataset.krakenWired = "1";
        btn.classList.add("kh-host");
        btn.title = "Case alerts";
        btn.addEventListener("click", function (e) {
          e.stopPropagation();
          if (openPop) { closePop(); return; }
          openBell(btn);
        });
        // badge turant bhar do (bina kholay bhi count dikhe)
        Promise.all([
          fetch("/api/stats").then(function (r) { return r.json(); }),
          fetch("/api/graph-data").then(function (r) { return r.json(); }),
          fetch("/api/syndicates").then(function (r) { return r.json(); }),
          fetch("/api/manual-log").then(function (r) { return r.json(); }),
          fetch("/api/dossiers").then(function (r) { return r.json(); })
        ]).then(function (all) {
          var rows = buildAlerts(all[0].stats || {}, all[1].predictions || [],
                                 all[2], all[3], all[4].targets || []);
          setBadge(btn, rows.length);
        }).catch(function () {});
      }

      if (name === "settings") {
        btn.dataset.krakenWired = "1";
        btn.classList.add("kh-host");
        btn.title = "Display and graph settings";
        btn.addEventListener("click", function (e) {
          e.stopPropagation();
          if (openPop) { closePop(); return; }
          openSettings();
        });
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wire);
  } else {
    wire();
  }
})();
