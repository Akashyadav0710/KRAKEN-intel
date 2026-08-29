from flask import Flask, render_template, jsonify, request
import os
import json

app = Flask(__name__)

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
# 2. Kraken API ROUTES (FULL HACKATHON DEMO MODE)
# ==========================================
@app.route('/api/graph-data', methods=['GET'])
def get_graph_data():
    try:
        entities = {"nodes": [], "edges": []}
        
        # 💥 GOLDEN DATA INJECTION: Poore 15 Asli Links for Predictions Page! 💥
        predictions = [
            {"source": "Timothy Dutt", "target": "Indrajit Pillay", "confidence": 99.0, "reasoning": "High network overlap (Common associates)"},
            {"source": "Acharya Road", "target": "Lakshit Luthra", "confidence": 99.0, "reasoning": "High network overlap (Financial trails)"},
            {"source": "Rachit Walla", "target": "Abhiram Varghese", "confidence": 99.0, "reasoning": "High network overlap (Telecom pings)"},
            {"source": "Rachit Walla", "target": "Kritika Buch", "confidence": 99.0, "reasoning": "High network overlap (Co-accused)"},
            {"source": "Rachit Walla", "target": "Bail Zila", "confidence": 99.0, "reasoning": "High network overlap (Financial trails)"},
            {"source": "Rachit Walla", "target": "Rehaan Bahl", "confidence": 99.0, "reasoning": "High network overlap (Common associates)"},
            {"source": "Rachit Walla", "target": "Charita Chowdhury", "confidence": 99.0, "reasoning": "High network overlap (Co-accused)"},
            {"source": "Rachit Walla", "target": "Timothy Dutt", "confidence": 99.0, "reasoning": "High network overlap (Telecom pings)"},
            {"source": "Advaith Yadav", "target": "Fiyaz Wali", "confidence": 99.0, "reasoning": "High network overlap (Financial trails)"},
            {"source": "Advaith Yadav", "target": "Abhiram Varghese", "confidence": 99.0, "reasoning": "High network overlap (Common associates)"},
            {"source": "Advaith Yadav", "target": "Varenya Bajaj", "confidence": 99.0, "reasoning": "High network overlap (Telecom pings)"},
            {"source": "Advaith Yadav", "target": "Vasa Road", "confidence": 99.0, "reasoning": "High network overlap (Co-accused)"},
            {"source": "Advaith Yadav", "target": "Kritika Buch", "confidence": 99.0, "reasoning": "High network overlap (Financial trails)"},
            {"source": "Advaith Yadav", "target": "Ishaan Patel", "confidence": 99.0, "reasoning": "High network overlap (Telecom pings)"},
            {"source": "Advaith Yadav", "target": "Bail Zila", "confidence": 99.0, "reasoning": "High network overlap (Common associates)"}
        ]
        
        # D3.js ko crash hone se bachane ke liye nodes mein in names ko inject kar rahe hain
        demo_names = list(set([p["source"] for p in predictions] + [p["target"] for p in predictions]))
        for n in demo_names:
            entities["nodes"].append({"id": n, "type": "person"})

        return jsonify({"status": "success", "entities": entities, "predictions": predictions})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/targets', methods=['GET'])
def get_targets():
    # 💥 GOLDEN DATA INJECTION: Syndicates aur Reports ke liye top Cartels!
    targets = [
        {"id": "Kraken-001", "name": "Advaith Yadav", "score": 98, "color": "error", "badge": "MASTERMIND", "phones": ["+919876543210"], "accounts": ["AC-102938"]},
        {"id": "Kraken-002", "name": "Rachit Walla", "score": 96, "color": "error", "badge": "HIGH VALUE", "phones": ["+918887776665"], "accounts": ["AC-564738"]},
        {"id": "Kraken-003", "name": "Ishaan Patel", "score": 92, "color": "error", "badge": "THREAT", "phones": ["+917778889990"], "accounts": ["AC-332211"]},
        {"id": "Kraken-004", "name": "Lakshit Luthra", "score": 88, "color": "secondary-container", "badge": "ASSOCIATE", "phones": ["+916665554443"], "accounts": []}
    ]
    return jsonify({"status": "success", "targets": targets})

@app.route('/api/update-graph', methods=['POST'])
def update_graph():
    return jsonify({"status": "success", "message": "Intel Processed"})

if __name__ == '__main__':
    app.run(debug=True)