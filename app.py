import os
import json
import random
import string
from datetime import datetime
from flask import Flask, render_template, request, jsonify, session
from flask_socketio import SocketIO, join_room, leave_room, emit
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'circuit_dark_network_key_999')
app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'static', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
DB_FILE = os.path.join(app.root_path, 'database.json')

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet', ping_timeout=60, ping_interval=25)

# --- BASE DE DONNÉES PERSISTANTE ---

def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "users": {},
        "messages": {},
        "private_messages": {},
        "circuits": {}
    }

def save_db(data):
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

db = load_db()

# Circuit général par défaut
if "1" not in db["circuits"]:
    db["circuits"]["1"] = {
        "id": "1",
        "name": "General // Mainnet",
        "code": "DARK01",
        "icon_url": "/static/default_avatar.png"
    }
    save_db(db)

def get_current_user():
    user = request.headers.get('X-Username') or session.get('username')
    return user if user and user in db["users"] else None

def generate_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

def get_private_room_id(user1, user2):
    return "room_" + "_".join(sorted([user1, user2]))

@app.route('/')
def index():
    return render_template('index.html')

# --- AUTHENTIFICATION & SESSIONS ---

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json or {}
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()

    if not username or not password:
        return jsonify({"error": "Identifiants manquants"}), 400

    if username in db["users"]:
        if db["users"][username]['password'] != password:
            return jsonify({"error": "Mot de passe incorrect"}), 401
    else:
        db["users"][username] = {
            "password": password,
            "avatar_url": "/static/default_avatar.png",
            "friends": [],
            "friend_requests": [],
            "is_online": True,
            "last_seen": datetime.now().strftime("%d/%m %H:%M")
        }
        save_db(db)

    session['username'] = username
    session.permanent = True

    db["users"][username].setdefault("friends", [])
    db["users"][username].setdefault("friend_requests", [])
    db["users"][username]["is_online"] = True
    save_db(db)

    return jsonify({
        "user": {
            "username": username,
            "avatar_url": db["users"][username].get("avatar_url", "/static/default_avatar.png"),
            "friends": db["users"][username].get("friends", []),
            "friend_requests": db["users"][username].get("friend_requests", [])
        }
    })

@app.route('/api/register', methods=['POST'])
def register():
    data = request.json or {}
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()

    if not username or not password:
        return jsonify({"error": "Champs invalides"}), 400

    if username in db["users"]:
        return jsonify({"error": "Nom d'utilisateur déjà pris"}), 400

    db["users"][username] = {
        "password": password,
        "avatar_url": "/static/default_avatar.png",
        "friends": [],
        "friend_requests": [],
        "is_online": True,
        "last_seen": datetime.now().strftime("%d/%m %H:%M")
    }
    save_db(db)

    session['username'] = username
    session.permanent = True
    return jsonify({"success": True})

@app.route('/api/logout', methods=['POST'])
def logout():
    username = get_current_user()
    if username and username in db["users"]:
        db["users"][username]["is_online"] = False
        db["users"][username]["last_seen"] = datetime.now().strftime("%d/%m %H:%M")
        save_db(db)
        socketio.emit('user_status_change', {'username': username, 'is_online': False}, broadcast=True)

    session.clear()
    return jsonify({"success": True})

# --- SYSTÈME D'AMIS (SNAPCHAT STYLE) ---

@app.route('/api/friends/send-request', methods=['POST'])
def send_friend_request():
    sender = get_current_user()
    if not sender:
        return jsonify({"error": "Non authentifié"}), 401

    data = request.json or {}
    target = data.get('target_user', '').strip()

    if not target or target not in db["users"]:
        return jsonify({"error": "Utilisateur introuvable"}), 404

    if target == sender:
        return jsonify({"error": "Vous ne pouvez pas vous ajouter vous-même"}), 400

    target_user_data = db["users"][target]
    sender_user_data = db["users"][sender]

    if sender in target_user_data.get("friends", []):
        return jsonify({"error": "Vous êtes déjà amis"}), 400

    if sender in target_user_data.get("friend_requests", []):
        return jsonify({"error": "Demande déjà envoyée"}), 400

    target_user_data.setdefault("friend_requests", []).append(sender)
    save_db(db)

    socketio.emit('notification_friend_request', {
        'from': sender,
        'avatar': sender_user_data.get("avatar_url", "/static/default_avatar.png")
    }, room=target)

    return jsonify({"success": True, "message": f"Demande envoyée à {target}"})

@app.route('/api/friends/accept-request', methods=['POST'])
def accept_friend_request():
    username = get_current_user()
    if not username:
        return jsonify({"error": "Non authentifié"}), 401

    data = request.json or {}
    sender = data.get('sender', '').strip()

    user_data = db["users"].get(username, {})
    if sender in user_data.get("friend_requests", []):
        user_data["friend_requests"].remove(sender)
        user_data.setdefault("friends", []).append(sender)

        db["users"][sender].setdefault("friends", []).append(username)
        save_db(db)

        socketio.emit('friend_request_accepted', {'by': username}, room=sender)
        return jsonify({"success": True})

    return jsonify({"error": "Demande introuvable"}), 404

@app.route('/api/friends/list', methods=['GET'])
def get_friends_list():
    username = get_current_user()
    if not username or username not in db["users"]:
        return jsonify([]), 401

    friends_usernames = db["users"][username].get("friends", [])
    friends_info = []

    for f in friends_usernames:
        f_data = db["users"].get(f, {})
        room_id = get_private_room_id(username, f)
        last_msg_list = db["private_messages"].get(room_id, [])
        last_msg = last_msg_list[-1] if last_msg_list else None

        friends_info.append({
            "username": f,
            "avatar_url": f_data.get("avatar_url", "/static/default_avatar.png"),
            "is_online": f_data.get("is_online", False),
            "last_seen": f_data.get("last_seen", "Jamais"),
            "last_message": last_msg.get("content", "Média") if last_msg else "Aucun message",
            "last_time": last_msg.get("timestamp", "") if last_msg else ""
        })

    return jsonify(friends_info)

@app.route('/api/friends/requests', methods=['GET'])
def get_friend_requests():
    username = get_current_user()
    if not username or username not in db["users"]:
        return jsonify([]), 401

    requests_usernames = db["users"][username].get("friend_requests", [])
    requests_info = [
        {
            "username": r,
            "avatar_url": db["users"].get(r, {}).get("avatar_url", "/static/default_avatar.png")
        }
        for r in requests_usernames
    ]
    return jsonify(requests_info)

# --- CIRCUITS & TCHATS PRIVÉS ---

@app.route('/api/my-circuits', methods=['GET'])
def get_circuits():
    return jsonify(list(db["circuits"].values()))

@app.route('/api/circuit/create', methods=['POST'])
def create_circuit():
    username = get_current_user()
    if not username:
        return jsonify({"error": "Non autorisé"}), 401

    data = request.json or {}
    name = data.get('name', 'Nouveau Circuit').strip()
    circuit_id = str(len(db["circuits"]) + 1)

    circuit = {
        "id": circuit_id,
        "name": name,
        "code": generate_code(),
        "icon_url": "/static/default_avatar.png"
    }

    db["circuits"][circuit_id] = circuit
    save_db(db)
    return jsonify({"circuit": circuit})

@app.route('/api/circuit/join', methods=['POST'])
def join_circuit_code():
    data = request.json or {}
    code = data.get('invite_code', '').strip().upper()

    for c_id, c in db["circuits"].items():
        if c['code'] == code:
            return jsonify({"success": True, "circuit": c})

    return jsonify({"error": "Code invalide ou introuvable"}), 404

@app.route('/api/circuit/<circuit_id>/members', methods=['GET'])
def get_members(circuit_id):
    members = [
        {
            "username": user,
            "avatar_url": info.get("avatar_url", "/static/default_avatar.png"),
            "is_online": info.get("is_online", False)
        }
        for user, info in db["users"].items()
    ]
    return jsonify(members)

@app.route('/api/circuit/<circuit_id>/messages', methods=['GET'])
def get_messages(circuit_id):
    return jsonify(db["messages"].get(circuit_id, []))

@app.route('/api/private-messages/<target_user>', methods=['GET'])
def get_private_messages(target_user):
    username = get_current_user()
    if not username:
        return jsonify([]), 401

    user_friends = db["users"].get(username, {}).get("friends", [])
    if target_user not in user_friends:
        return jsonify({"error": "Vous devez être amis pour lire ces messages"}), 403

    room_id = get_private_room_id(username, target_user)
    msgs = db["private_messages"].get(room_id, [])

    updated = False
    for m in msgs:
        if m.get("target") == username and not m.get("read"):
            m["read"] = True
            updated = True

    if updated:
        save_db(db)

    return jsonify(msgs)

# --- UPLOADS ---

@app.route('/api/upload-avatar', methods=['POST'])
def upload_avatar():
    file = request.files.get('avatar')
    username = get_current_user()
    if not file or not username:
        return jsonify({"error": "Erreur d'upload"}), 400

    filename = secure_filename(f"avatar_{username}_{file.filename}")
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)

    url = f"/static/uploads/{filename}"
    if username in db["users"]:
        db["users"][username]["avatar_url"] = url
        save_db(db)

    return jsonify({"avatar_url": url})

@app.route('/api/upload-media', methods=['POST'])
def upload_media():
    file = request.files.get('media')
    if not file:
        return jsonify({"error": "Aucun fichier"}), 400

    filename = secure_filename(f"media_{file.filename}")
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)
    return jsonify({"media_url": f"/static/uploads/{filename}"})

# --- WEBSOCKETS EN TEMPS RÉEL ---

@socketio.on('join_user_session')
def handle_user_session(data=None):
    data = data or {}
    username = data.get('username') or session.get('username')
    if username and username in db["users"]:
        session['username'] = username
        db["users"][username]["is_online"] = True
        save_db(db)
        join_room(username)
        emit('user_status_change', {'username': username, 'is_online': True}, broadcast=True)

@socketio.on('typing')
def handle_typing(data):
    target_user = data.get('target_user')
    sender = session.get('username') or data.get('sender')
    if target_user and sender:
        emit('user_typing', {'sender': sender, 'is_typing': data.get('is_typing', False)}, room=target_user)

@socketio.on('join_circuit')
def handle_join(data):
    circuit_id = str(data.get('circuit_id'))
    join_room(circuit_id)

@socketio.on('leave_circuit')
def handle_leave(data):
    circuit_id = str(data.get('circuit_id'))
    leave_room(circuit_id)

@socketio.on('join_private_chat')
def handle_join_private(data):
    username = session.get('username') or data.get('my_username')
    target_user = data.get('target_user')
    if username and target_user:
        if target_user in db["users"].get(username, {}).get("friends", []):
            room_id = get_private_room_id(username, target_user)
            join_room(room_id)

@socketio.on('send_message')
def handle_message(data):
    circuit_id = str(data.get('circuit_id'))
    username = session.get('username') or data.get('sender', 'Anonyme')
    user_avatar = db["users"].get(username, {}).get('avatar_url', '/static/default_avatar.png')
    now = datetime.now().strftime("%H:%M")

    msg = {
        "circuit_id": circuit_id,
        "sender": username,
        "content": data.get('content', ''),
        "media_url": data.get('media_url', None),
        "avatar": user_avatar,
        "timestamp": now
    }

    if circuit_id not in db["messages"]:
        db["messages"][circuit_id] = []

    db["messages"][circuit_id].append(msg)
    save_db(db)

    emit('new_message', msg, room=circuit_id)

@socketio.on('send_private_message')
def handle_private_message(data):
    sender = session.get('username') or data.get('sender')
    target_user = data.get('target_user')

    if not sender or not target_user:
        return

    if target_user not in db["users"].get(sender, {}).get("friends", []):
        emit('error_message', {'message': "Vous devez être amis pour envoyer un message."})
        return

    room_id = get_private_room_id(sender, target_user)
    sender_avatar = db["users"].get(sender, {}).get('avatar_url', '/static/default_avatar.png')
    now = datetime.now().strftime("%H:%M")

    msg = {
        "sender": sender,
        "target": target_user,
        "content": data.get('content', ''),
        "media_url": data.get('media_url', None),
        "avatar": sender_avatar,
        "timestamp": now,
        "read": False
    }

    if room_id not in db["private_messages"]:
        db["private_messages"][room_id] = []

    db["private_messages"][room_id].append(msg)
    save_db(db)

    emit('new_private_message', msg, room=room_id)

    emit('notification_new_message', {
        'from': sender,
        'content': data.get('content', 'A envoyé un média'),
        'avatar': sender_avatar,
        'timestamp': now
    }, room=target_user)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False)
