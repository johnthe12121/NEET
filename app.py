import sqlite3
import json
import uuid
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template, session
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = 'super_secret_neet_key_replace_me_in_production'
app.permanent_session_lifetime = timedelta(days=7)

DATABASE = 'neet_app.db'

def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    conn.execute('''CREATE TABLE IF NOT EXISTS users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT UNIQUE NOT NULL,
                        password TEXT NOT NULL,
                        is_admin INTEGER DEFAULT 0,
                        history TEXT DEFAULT '[]'
                    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS tests (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        data TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )''')
    
    admin = conn.execute('SELECT * FROM users WHERE username = ?', ('admin',)).fetchone()
    if not admin:
        conn.execute('INSERT INTO users (username, password, is_admin) VALUES (?, ?, 1)',
                     ('admin', generate_password_hash('admin123')))
    conn.commit()
    conn.close()

init_db()

# --- PAGE ROUTES ---
@app.route('/')
def home():
    return render_template('index.html')

@app.route('/admin')
def admin_page():
    return render_template('admin.html')

# --- AUTH API ---
@app.route('/api/auth/status', methods=['GET'])
def auth_status():
    if 'user_id' in session:
        return jsonify({"logged_in": True, "username": session.get('username'), "is_admin": session.get('is_admin')})
    return jsonify({"logged_in": False})

@app.route('/api/auth/register', methods=['POST'])
def register():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    conn = get_db_connection()
    if conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone():
        conn.close()
        return jsonify({"error": "Username already exists"}), 400
        
    cursor = conn.execute('INSERT INTO users (username, password) VALUES (?, ?)', 
                          (username, generate_password_hash(password)))
    conn.commit()
    
    session.permanent = True
    session['user_id'] = cursor.lastrowid
    session['username'] = username
    session['is_admin'] = 0
    conn.close()
    return jsonify({"message": "Registration successful"})

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json
    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE username = ?', (data.get('username'),)).fetchone()
    conn.close()
    
    if user and check_password_hash(user['password'], data.get('password')):
        session.permanent = True
        session['user_id'] = user['id']
        session['username'] = user['username']
        session['is_admin'] = user['is_admin']
        return jsonify({"message": "Logged in", "is_admin": user['is_admin']})
    return jsonify({"error": "Invalid credentials"}), 401

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({"message": "Logged out"})

# --- STUDENT API ---
@app.route('/api/dashboard', methods=['GET'])
def student_dashboard():
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401
    
    conn = get_db_connection()
    tests = conn.execute('SELECT id, title, created_at FROM tests ORDER BY created_at DESC').fetchall()
    user = conn.execute('SELECT history FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    conn.close()
    
    return jsonify({
        "tests": [dict(t) for t in tests],
        "history": json.loads(user['history']) if user['history'] else []
    })

@app.route('/api/test/<test_id>', methods=['GET'])
def get_test(test_id):
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401
    
    conn = get_db_connection()
    test = conn.execute('SELECT * FROM tests WHERE id = ?', (test_id,)).fetchone()
    conn.close()
    
    if not test: return jsonify({"error": "Test not found"}), 404
    
    questions = json.loads(test['data'])
    safe_questions = [{"id": q["id"], "text": q["text"], "options": q["options"]} for q in questions]
    return jsonify({"title": test['title'], "questions": safe_questions})

@app.route('/api/submit/<test_id>', methods=['POST'])
def submit_test(test_id):
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401

    conn = get_db_connection()
    test = conn.execute('SELECT * FROM tests WHERE id = ?', (test_id,)).fetchone()
    if not test: 
        conn.close()
        return jsonify({"error": "Test not found"}), 404

    questions = json.loads(test['data'])
    user_answers = request.json.get('answers', {})
    
    analysis = []
    correct_count = sum(1 for q in questions if user_answers.get(q["id"]) == q["answer"])
    
    for q in questions:
        is_correct = (user_answers.get(q["id"]) == q["answer"])
        analysis.append({
            "question": q["text"], "your_answer": user_answers.get(q["id"], "Not Answered"),
            "correct_answer": q["answer"], "is_correct": is_correct
        })
        
    percentage = round((correct_count / len(questions)) * 100, 2)
    
    user = conn.execute('SELECT history FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    history = json.loads(user['history']) if user['history'] else []
    
    history_entry = {
        "test_id": test_id,
        "title": test['title'],
        "score": correct_count,
        "total": len(questions),
        "percentage": percentage,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M")
    }
    history.append(history_entry)
    
    conn.execute('UPDATE users SET history = ? WHERE id = ?', (json.dumps(history), session['user_id']))
    conn.commit()
    conn.close()

    return jsonify({"score": correct_count, "total": len(questions), "percentage": percentage, 
                    "analysis": analysis, "history": history})

# --- ADMIN API (CRUD) ---
@app.route('/api/admin/data', methods=['GET'])
def admin_data():
    if not session.get('is_admin'): return jsonify({"error": "Unauthorized"}), 403
    
    conn = get_db_connection()
    tests = conn.execute('SELECT id, title, created_at FROM tests ORDER BY created_at DESC').fetchall()
    users = conn.execute('SELECT id, username, history FROM users WHERE is_admin = 0').fetchall()
    conn.close()
    
    return jsonify({
        "tests": [dict(t) for t in tests],
        "users": [{"id": u["id"], "username": u["username"], "history": json.loads(u["history"])} for u in users]
    })

@app.route('/api/admin/tests', methods=['POST'])
def add_test():
    if not session.get('is_admin'): return jsonify({"error": "Unauthorized"}), 403
    
    data = request.json
    test_id = str(uuid.uuid4())[:8]
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    conn = get_db_connection()
    conn.execute('INSERT INTO tests (id, title, data, created_at) VALUES (?, ?, ?, ?)',
                 (test_id, data['title'], json.dumps(data['questions']), created_at))
    conn.commit()
    conn.close()
    return jsonify({"message": "Test created", "test_id": test_id})

@app.route('/api/admin/tests/<test_id>', methods=['DELETE'])
def delete_test(test_id):
    if not session.get('is_admin'): return jsonify({"error": "Unauthorized"}), 403
    
    conn = get_db_connection()
    conn.execute('DELETE FROM tests WHERE id = ?', (test_id,))
    conn.commit()
    conn.close()
    return jsonify({"message": "Test deleted"})

@app.route('/api/admin/users', methods=['POST'])
def add_user():
    """Admin endpoint to create user accounts manually"""
    if not session.get('is_admin'): return jsonify({"error": "Unauthorized"}), 403
    
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    conn = get_db_connection()
    try:
        conn.execute('INSERT INTO users (username, password) VALUES (?, ?)', 
                     (username, generate_password_hash(password)))
        conn.commit()
        return jsonify({"message": "User created successfully"})
    except sqlite3.IntegrityError:
        return jsonify({"error": "Username already exists"}), 400
    finally:
        conn.close()

if __name__ == '__main__':
    app.run(debug=True, port=5000)