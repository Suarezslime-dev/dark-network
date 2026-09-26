import os
import random
import string
from flask import Flask, render_template, request, jsonify, session
from flask_socketio import SocketIO, join_room, leave_room, emit
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'circuit_dark_network_key_999')
app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'static', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # Limite d'upload à 16MB

# Création automatique du dossier d'upload s'il n'existe pas
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Initialisation SocketIO avec support CORS complet
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# Base de données temporaire en mémoire
USERS = {}             # {username: {"password": p, "avatar_url": url}}
CIRCUITS = {}          # {id: {"id": id, "name": name, "code": code, "background_url": url, "icon_url": url}}
CIRCUIT_MEMBERS = {}   # {circuit_id: set(username1, username2)}
MESSAGES = {}          # {circuit_id: [msg1, msg2]}
PRIVATE_MESSAGES = []  # [{sender, receiver, content, media_url, avatar}]


def generate_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))


def init_default_circuit():
    if not CIRCUITS:
        c_id = "1"
        CIRCUITS[c_id] = {
            "id": c_id,
            "name": "General // Mainnet",
            "code": "DARK01",
            "background_url": "/static/bg-pirate.jpg",
            "icon_url": "/static/default_avatar.png"
        }
        CIRCUIT_MEMBERS[c_id] = set()
        MESSAGES[c_id] = []


init_default_circuit()

# --- ROUTES HTML ---

@app.route('/')
def index():
    return render_template('index.html')

# --- AUTHENTIFICATION & UTILISATEURS ---

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json or {}
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()

    if not username or not password:
        return jsonify({"error": "Identifiants manquants"}), 400

    if username in USERS:
        if USERS[username]['password'] != password:
            return jsonify({"error": "Mot de passe incorrect"}), 401
    else:
        # Création automatique au premier login
        USERS[username] = {
            "password": password,
            "avatar_url": "/static/default_avatar.png"
        }

    session['username'] = username
    session.permanent = True

    # Auto-ajout au circuit général
    if "1" in CIRCUIT_MEMBERS:
        CIRCUIT_MEMBERS["1"].add(username)

    return jsonify({
        "user": {
            "username": username,
            "avatar_url": USERS[username]["avatar_url"]
        }
    })


@app.route('/api/register', methods=['POST'])
def register():
    data = request.json or {}
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()

    if not username or not password:
        return jsonify({"error": "Champs invalides"}), 400

    if username in USERS:
        return jsonify({"error": "Nom d'utilisateur déjà pris"}), 400

    USERS[username] = {
        "password": password,
        "avatar_url": "/static/default_avatar.png"
    }
    session['username'] = username
    session.permanent = True

    if "1" in CIRCUIT_MEMBERS:
        CIRCUIT_MEMBERS["1"].add(username)

    return jsonify({"success": True})


@app.route('/api/users', methods=['GET'])
def get_users():
    current_user = session.get('username')
    if not current_user:
        return jsonify([]), 401

    # Retourne tous les utilisateurs sauf soi-même
    user_list = [
        {"username": u, "avatar_url": data.get("avatar_url", "/static/default_avatar.png")}
        for u, data in USERS.items()
        if u != current_user
    ]
    return jsonify(user_list)

# --- GESTION DES CIRCUITS ---

@app.route('/api/my-circuits', methods=['GET'])
def get_circuits():
    username = session.get('username')
    if not username:
        return jsonify([]), 401

    user_circuits = [
        CIRCUITS[c_id] for c_id, members in CIRCUIT_MEMBERS.items()
        if username in members and c_id in CIRCUITS
    ]
    return jsonify(user_circuits)


@app.route('/api/circuit/create', methods=['POST'])
def create_circuit():
    username = session.get('username')
    if not username:
        return jsonify({"error": "Non autorisé"}), 401

    data = request.json or {}
    name = data.get('name', 'Nouveau Circuit').strip()
    circuit_id = str(len(CIRCUITS) + 1)

    circuit = {
        "id": circuit_id,
        "name": name,
        "code": generate_code(),
        "background_url": "/static/bg-pirate.jpg",
        "icon_url": "/static/default_avatar.png"
    }

    CIRCUITS[circuit_id] = circuit
    MESSAGES[circuit_id] = []
    CIRCUIT_MEMBERS[circuit_id] = {username}

    return jsonify({"circuit": circuit})


@app.route('/api/circuit/join', methods=['POST'])
def join_circuit_code():
    username = session.get('username')
    if not username:
        return jsonify({"error": "Non autorisé"}), 401

    data = request.json or {}
    code = data.get('invite_code', '').strip().upper()

    for c_id, c in CIRCUITS.items():
        if c['code'] == code:
            CIRCUIT_MEMBERS[c_id].add(username)
            return jsonify({"success": True, "circuit": c})

    return jsonify({"error": "Code invalide ou introuvable"}), 404


@app.route('/api/circuit/<circuit_id>/members', methods=['GET'])
def get_members(circuit_id):
    if circuit_id not in CIRCUIT_MEMBERS:
        return jsonify([])

    members = [
        {
            "username": user,
            "avatar_url": USERS.get(user, {}).get("avatar_url", "/static/default_avatar.png"),
            "is_online": True
        }
        for user in CIRCUIT_MEMBERS[circuit_id]
    ]
    return jsonify(members)


@app.route('/api/circuit/<circuit_id>/messages', methods=['GET'])
def get_messages(circuit_id):
    return jsonify(MESSAGES.get(circuit_id, []))


@app.route('/api/private-messages/<target_user>', methods=['GET'])
def get_private_messages(target_user):
    username = session.get('username')
    if not username:
        return jsonify([]), 401

    # Récupérer l'historique entre l'utilisateur actuel et le destinataire
    history = [
        m for m in PRIVATE_MESSAGES
        if (m['sender'] == username and m['receiver'] == target_user) or
           (m['sender'] == target_user and m['receiver'] == username)
    ]
    return jsonify(history)

# --- UPLOADS DE FICHIERS & MÉDIAS ---

@app.route('/api/upload-avatar', methods=['POST'])
def upload_avatar():
    file = request.files.get('avatar')
    username = session.get('username')
    if not file or not username:
        return jsonify({"error": "Erreur d'upload"}), 400

    filename = secure_filename(f"avatar_{username}_{file.filename}")
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)

    url = f"/static/uploads/{filename}"
    if username in USERS:
        USERS[username]["avatar_url"] = url

    return jsonify({"avatar_url": url})


@app.route('/api/circuit/<circuit_id>/upload-background', methods=['POST'])
def upload_bg(circuit_id):
    file = request.files.get('background')
    if not file or circuit_id not in CIRCUITS:
        return jsonify({"error": "Erreur d'upload"}), 400

    filename = secure_filename(f"bg_{circuit_id}_{file.filename}")
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)

    url = f"/static/uploads/{filename}"
    CIRCUITS[circuit_id]['background_url'] = url

    socketio.emit('background_updated', {'circuit_id': circuit_id, 'background_url': url}, room=str(circuit_id))
    return jsonify({"background_url": url})


@app.route('/api/circuit/<circuit_id>/upload-icon', methods=['POST'])
def upload_circuit_icon(circuit_id):
    file = request.files.get('icon')
    if not file or circuit_id not in CIRCUITS:
        return jsonify({"error": "Erreur d'upload"}), 400

    filename = secure_filename(f"icon_{circuit_id}_{file.filename}")
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)

    url = f"/static/uploads/{filename}"
    CIRCUITS[circuit_id]['icon_url'] = url

    socketio.emit('circuit_icon_updated', {'circuit_id': circuit_id, 'icon_url': url})
    return jsonify({"icon_url": url})


@app.route('/api/upload-media', methods=['POST'])
def upload_media():
    file = request.files.get('media')
    if not file:
        return jsonify({"error": "Aucun fichier"}), 400

    filename = secure_filename(f"media_{file.filename}")
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)
    return jsonify({"media_url": f"/static/uploads/{filename}"})

# --- ÉVÉNEMENTS WEBSOCKETS EN TEMPS RÉEL ---

@socketio.on('join_circuit')
def handle_join(data):
    circuit_id = str(data.get('circuit_id'))
    join_room(circuit_id)

    username = session.get('username')
    if username and circuit_id in CIRCUIT_MEMBERS:
        CIRCUIT_MEMBERS[circuit_id].add(username)
        emit('presence_update', {'circuit_id': circuit_id}, room=circuit_id)


@socketio.on('leave_circuit')
def handle_leave(data):
    circuit_id = str(data.get('circuit_id'))
    leave_room(circuit_id)


@socketio.on('join_private_chat')
def handle_join_private(data):
    username = session.get('username')
    if username:
        join_room(username)  # Chaque utilisateur rejoint une room à son propre nom


@socketio.on('send_message')
def handle_message(data):
    circuit_id = str(data.get('circuit_id'))
    username = session.get('username', 'Anonyme')
    user_avatar = USERS.get(username, {}).get('avatar_url', '/static/default_avatar.png')

    msg = {
        "circuit_id": circuit_id,
        "sender": username,
        "content": data.get('content', ''),
        "media_url": data.get('media_url', None),
        "avatar": user_avatar
    }

    if circuit_id in MESSAGES:
        MESSAGES[circuit_id].append(msg)

    emit('new_message', msg, room=circuit_id)


@socketio.on('send_private_message')
def handle_private_message(data):
    sender = session.get('username')
    target_user = data.get('target_user')
    if not sender or not target_user:
        return

    sender_avatar = USERS.get(sender, {}).get('avatar_url', '/static/default_avatar.png')

    msg = {
        "sender": sender,
        "receiver": target_user,
        "content": data.get('content', ''),
        "media_url": data.get('media_url', None),
        "avatar": sender_avatar
    }

    PRIVATE_MESSAGES.append(msg)

    # Envoie le message au destinataire et à l'expéditeur
    emit('new_private_message', msg, room=target_user)
    emit('new_private_message', msg, room=sender)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False)
