from flask import Flask, render_template, jsonify, request, session, url_for
import os
import secrets
import signal
import threading
import time

import kraken_data

app = Flask(__name__)

# ==========================================
# 0. CLEARANCE / SESSION SETUP
# ==========================================
# Secret key env se lo; na mile to har launch pe naya banega -> server band
# hone ke baad purane session cookies apne aap dead ho jate hain.
app.secret_key = os.environ.get("KRAKEN_SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

# Demo mode: koi bhi non-empty Clearance ID + Biometric Hash chalega.
# Lock down karna ho to launch se pehle env set karo:
#   export KRAKEN_CLEARANCE_ID="AXX-993-1121"
#   export KRAKEN_BIOMETRIC_KEY="<hash>"
EXPECTED_CLEARANCE_ID = os.environ.get("KRAKEN_CLEARANCE_ID")
EXPECTED_BIOMETRIC_KEY = os.environ.get("KRAKEN_BIOMETRIC_KEY")

# NOTE: Koi clearance gate nahi hai. Saare pages seedhe khulte hain -- bar bar
# password maangna demo ke beech irritating tha. Login screen sirf dikhane ke
# liye hai (/login), wo kisi page ko block nahi karti.


def _safe_next(target):
    """Open-redirect se bachne ke liye sirf apne hi relative paths allow karo."""
    if not target or not target.startswith("/") or target.startswith("//"):
        return None
    if target in ("/", "/login"):
        return None
    return target

# --- BROWSER CACHE BUSTER (Taaki refresh pe turant update ho) ---
@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '-1'
    return response

# ==========================================
# 1. HTML PAGE ROUTES
# ==========================================
@app.route('/')
@app.route('/command')
def command(): return render_template('command.html')
@app.route('/login')
def login(): return render_template('login.html')   # optional demo screen
@app.route('/network')
def network(): return render_template('network.html')
@app.route('/predictions')
def predictions(): return render_template('predictions.html')
@app.route('/syndicates')
def syndicates(): return render_template('syndicates.html')
@app.route('/intake')
def intake(): return render_template('intake.html')
@app.route('/profile')
def profile(): return render_template('profile.html')
@app.route('/reports')
def reports(): return render_template('reports.html')

# ==========================================
# 1b. CLEARANCE HANDSHAKE
# ==========================================
@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json(silent=True) or request.form or {}
    clearance = (data.get('clearance_id') or '').strip()
    biometric = (data.get('biometric_key') or '').strip()

    if not clearance or not biometric:
        return jsonify({
            "status": "error",
            "message": "CLEARANCE ID AND BIOMETRIC HASH BOTH REQUIRED"
        }), 400

    for supplied, expected in ((clearance, EXPECTED_CLEARANCE_ID), (biometric, EXPECTED_BIOMETRIC_KEY)):
        if expected and not secrets.compare_digest(supplied, expected):
            return jsonify({
                "status": "error",
                "message": "ACCESS DENIED - INVALID CLEARANCE"
            }), 401

    session.clear()
    session['kraken_auth'] = True
    session['operator'] = clearance
    session.permanent = False

    return jsonify({
        "status": "success",
        "operator": clearance,
        "redirect": _safe_next(data.get('next')) or url_for('command')
    })


# ==========================================
# 2. Kraken API ROUTES  (asli data -- kraken_data.py se)
# ==========================================
# Pehle yahan hardcoded demo list thi aur edges khaali [] jaate the, isliye
# network graph ko khud random links banane padte the. Ab saara data
# sample_data/extracted_entities.json se aata hai, ek hi graph se.

@app.route('/api/graph-data', methods=['GET'])
def get_graph_data():
    try:
        min_weight = request.args.get('min_weight', default=1, type=int) or 1
        tower_min = request.args.get('tower_min', default=3, type=int)
        include_devices = request.args.get('devices', '1') != '0'
        top = request.args.get('top', default=15, type=int) or 15

        entities = kraken_data.graph_payload(
            min_weight=min_weight,
            include_devices=include_devices,
            tower_min=tower_min,
        )
        return jsonify({
            "status": "success",
            "entities": entities,
            "predictions": kraken_data.predictions(top),
            "stats": kraken_data.stats(),
        })
    except Exception as exc:
        app.logger.exception("graph-data failed")
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route('/api/targets', methods=['GET'])
def get_targets():
    try:
        top = request.args.get('top', default=12, type=int) or 12
        return jsonify({"status": "success", "targets": kraken_data.targets(top)})
    except Exception as exc:
        app.logger.exception("targets failed")
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route('/api/dossiers', methods=['GET'])
def get_dossiers():
    """Profile page ke liye har entity ka asli dossier."""
    try:
        return jsonify({"status": "success", "targets": kraken_data.dossiers()})
    except Exception as exc:
        app.logger.exception("dossiers failed")
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route('/api/entity', methods=['GET'])
def get_entity():
    """Ek target ka asli record: CDR rows, transactions, associates, AI links."""
    try:
        name = (request.args.get('name') or '').strip()
        if not name:
            return jsonify({"status": "error", "message": "An entity name is required."}), 400
        record = kraken_data.entity(name)
        if record is None:
            return jsonify({"status": "error", "message": "Entity not found in graph: %s" % name}), 404
        return jsonify({"status": "success", "entity": record})
    except Exception as exc:
        app.logger.exception("entity failed")
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route('/api/syndicates', methods=['GET'])
def get_syndicates():
    """
    Asli syndicates -- Louvain community detection confirmed links par.
    Pehle ye page AI predictions ko source ke hisaab se group karta tha, jo
    actually cartel nahi tha, sirf sorting ka side effect tha.
    """
    try:
        resolution = request.args.get('resolution', default=1.0, type=float) or 1.0
        resolution = min(max(resolution, 0.2), 4.0)
        return jsonify({"status": "success", **kraken_data.syndicates(resolution)})
    except Exception as exc:
        app.logger.exception("syndicates failed")
        return jsonify({"status": "error", "message": str(exc)}), 500


# ---------- Manual graph editing (Intake page ka ledger) ----------

@app.route('/api/manual-log', methods=['GET'])
def get_manual_log():
    """Kya manually add hua, kya hataya gaya -- Intake ledger isse bharta hai."""
    try:
        return jsonify({"status": "success", **kraken_data.manual_log()})
    except Exception as exc:
        app.logger.exception("manual-log failed")
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route('/api/remove-entity', methods=['POST'])
def remove_entity():
    """Entity aur uske saare links graph se hata do (file nahi badalti)."""
    try:
        data = request.get_json(silent=True) or {}
        name = (data.get('name') or '').strip()
        if not name:
            return jsonify({"status": "error", "message": "An entity name is required."}), 400
        result = kraken_data.remove_entity(name)
        if result is None:
            return jsonify({"status": "error", "message": "Entity not found in graph: %s" % name}), 404
        return jsonify({"status": "success", **result})
    except Exception as exc:
        app.logger.exception("remove-entity failed")
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route('/api/remove-link', methods=['POST'])
def remove_link():
    """Do logon ke beech ka ek link hata do."""
    try:
        data = request.get_json(silent=True) or {}
        result = kraken_data.remove_link((data.get('a') or '').strip(),
                                         (data.get('b') or '').strip())
        if result is None:
            return jsonify({"status": "error", "message": "Both entities must exist in the graph."}), 404
        return jsonify({"status": "success", **result})
    except Exception as exc:
        app.logger.exception("remove-link failed")
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route('/api/restore-entity', methods=['POST'])
def restore_entity():
    """Hataya hua entity wapas. `all: true` bheja to sab restore."""
    try:
        data = request.get_json(silent=True) or {}
        if data.get('all'):
            return jsonify({"status": "success", **kraken_data.restore_all()})
        name = (data.get('name') or '').strip()
        result = kraken_data.restore_entity(name)
        if result is None:
            return jsonify({"status": "error", "message": "Entity is not in the removed list: %s" % name}), 404
        return jsonify({"status": "success", **result})
    except Exception as exc:
        app.logger.exception("restore-entity failed")
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route('/api/undo-intake', methods=['POST'])
def undo_intake():
    """Aakhri upload poora undo."""
    try:
        result = kraken_data.undo_last_intake()
        if result is None:
            return jsonify({"status": "error", "message": "No uploads available to undo."}), 404
        return jsonify({"status": "success", **result})
    except Exception as exc:
        app.logger.exception("undo-intake failed")
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route('/api/reset-graph', methods=['POST'])
def reset_graph():
    """Saare manual edits wipe -- graph wapas sirf source JSON jaisa."""
    try:
        return jsonify({"status": "success", **kraken_data.reset_state()})
    except Exception as exc:
        app.logger.exception("reset-graph failed")
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route('/api/path', methods=['GET'])
def get_path():
    """Do entities ke beech ka shortest evidence chain."""
    try:
        start = (request.args.get('from') or '').strip()
        end = (request.args.get('to') or '').strip()
        if not start or not end:
            return jsonify({"status": "error", "message": "Both 'from' and 'to' are required."}), 400
        devices = request.args.get('devices', '1') != '0'
        tower_min = request.args.get('tower_min', default=3, type=int)
        result = kraken_data.shortest_path(start, end, include_devices=devices, tower_min=tower_min)
        return jsonify({"status": "success", **result})
    except Exception as exc:
        app.logger.exception("path failed")
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route('/api/search', methods=['GET'])
def get_search():
    """Path finder ke input boxes ka autocomplete."""
    try:
        term = (request.args.get('q') or '').strip()
        return jsonify({"status": "success", "results": kraken_data.search_entities(term)})
    except Exception as exc:
        app.logger.exception("search failed")
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route('/api/timeline', methods=['GET'])
def get_timeline():
    """Month-wise call aur money activity (poore case ki ya ek target ki)."""
    try:
        name = (request.args.get('name') or '').strip() or None
        data = kraken_data.timeline(name)
        if data is None:
            return jsonify({"status": "error", "message": "Entity not found in graph: %s" % name}), 404
        return jsonify({"status": "success", **data})
    except Exception as exc:
        app.logger.exception("timeline failed")
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route('/api/stats', methods=['GET'])
def get_stats():
    """Command/reports ke counters -- ek hi source se, isliye kabhi mismatch nahi."""
    try:
        return jsonify({"status": "success", "stats": kraken_data.stats()})
    except Exception as exc:
        app.logger.exception("stats failed")
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route('/api/update-graph', methods=['POST'])
def update_graph():
    """
    Intake page se aaya naya intel. Pehle ye sirf "success" bolta tha aur data
    kahin jaata hi nahi tha -- ab live graph mein merge hota hai, to Network
    page turant naye nodes/links dikhata hai.
    """
    try:
        data = request.get_json(silent=True) or {}
        names = data.get('names') or []
        phones = data.get('phones') or []
        banks = data.get('banks') or data.get('accounts') or []

        before = kraken_data.stats()
        merged = kraken_data.add_intake(names, phones, banks)

        # Khaali payload par "merged" bolna galat tha -- ab saaf mana karo.
        if merged is None:
            return jsonify({
                "status": "error",
                "message": "No entities supplied. Provide at least one name, "
                           "phone number or account."
            }), 400

        after = kraken_data.stats()
        added = {
            "persons": after["persons"] - before["persons"],
            "phones": after["phones"] - before["phones"],
            "accounts": after["accounts"] - before["accounts"],
            "links": after["confirmed_links"] - before["confirmed_links"],
        }

        if sum(added.values()) == 0:
            # Kuch bheja to tha, lekin sab pehle se graph mein hai
            message = "Nothing new: every entity supplied is already in the graph."
        else:
            parts = []
            for key, label in (("persons", "person"), ("phones", "SIM"),
                               ("accounts", "account"), ("links", "link")):
                if added[key]:
                    parts.append("%d %s%s" % (added[key], label,
                                              "" if added[key] == 1 else "s"))
            message = "Merged into the live graph: %s." % ", ".join(parts)

        return jsonify({
            "status": "success",
            "message": message,
            "added": added,
            "stats": after,
        })
    except Exception as exc:
        app.logger.exception("update-graph failed")
        return jsonify({"status": "error", "message": str(exc)}), 500


# ==========================================
# 3. SECURE LOGOUT --> SESSION REVOKE + BACK TO LOGIN
# ==========================================
# Default: sirf clearance revoke hoti hai, server chalta rehta hai aur operator
# wapas /login par aa jata hai. Poora process band karna ho (purana behaviour)
# to launch se pehle: export KRAKEN_LOGOUT_SHUTDOWN=1
LOGOUT_SHUTS_DOWN = os.environ.get("KRAKEN_LOGOUT_SHUTDOWN", "").strip().lower() in ("1", "true", "yes", "on")


def _terminate_kraken_core():
    """Kill the Kraken process completely (reloader parent included)."""
    time.sleep(0.4)  # response ko flush hone do, phir shutdown
    try:
        # debug=True ke reloader child se parent supervisor ko bhi band karo,
        # warna wo turant naya server spawn kar dega.
        if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
            parent = os.getppid()
            if parent > 1:
                os.kill(parent, signal.SIGTERM)
    except Exception:
        pass
    os._exit(0)


@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()  # server-side clearance revoke

    if LOGOUT_SHUTS_DOWN:
        threading.Thread(target=_terminate_kraken_core, daemon=True).start()
        return jsonify({
            "status": "success",
            "mode": "shutdown",
            "message": "Session revoked. Kraken core shutting down."
        })

    return jsonify({
        "status": "success",
        "mode": "redirect",
        "redirect": url_for('login'),
        "message": "Clearance revoked. Returning to secure access."
    })


if __name__ == '__main__':
    app.run(debug=True)