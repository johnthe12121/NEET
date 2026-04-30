import os
import json
import uuid
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template, session, redirect
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

# Load environment variables from a hidden .env file (if it exists)
load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'super_secret_neet_key_replace_me_in_production')

# Safely fetch the URL without hardcoding it in the script
DATABASE_URL = os.environ.get('DATABASE_URL')

def get_db_connection():
    # Connect to the remote PostgreSQL database
    conn = psycopg2.connect(DATABASE_URL)
    return conn

def init_db():
    if not DATABASE_URL:
        print("WARNING: No DATABASE_URL found. Please set it in your .env file or Render dashboard!")
        return

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    # PostgreSQL uses SERIAL for auto-incrementing primary keys
    cur.execute('''CREATE TABLE IF NOT EXISTS users (
                        id SERIAL PRIMARY KEY,
                        username TEXT UNIQUE NOT NULL,
                        password TEXT NOT NULL,
                        is_admin INTEGER DEFAULT 0,
                        history TEXT DEFAULT '[]'
                    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS tests (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        data TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )''')
    
    # Check for default admin
    cur.execute('SELECT * FROM users WHERE username = %s', ('admin',))
    admin = cur.fetchone()
    if not admin:
        cur.execute('INSERT INTO users (username, password, is_admin) VALUES (%s, %s, 1)',
                     ('admin', generate_password_hash('admin123')))
    
    conn.commit()
    cur.close()
    conn.close()

# Initialize the PostgreSQL tables on startup
if DATABASE_URL:
    init_db()

# --- PAGE ROUTES ---
@app.route('/')
def home():
    if 'user_id' not in session:
        return redirect('/login')
    if session.get('is_admin'):
        return redirect('/admin')
    return render_template('index.html')

@app.route('/login')
def login_page():
    if 'user_id' in session:
        return redirect('/')
    return render_template('login.html')

@app.route('/admin')
def admin_page():
    if not session.get('is_admin'):
        return redirect('/login')
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
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    cur.execute('SELECT * FROM users WHERE username = %s', (username,))
    if cur.fetchone():
        cur.close()
        conn.close()
        return jsonify({"error": "Username already exists"}), 400
        
    # PostgreSQL uses RETURNING id to get the generated ID
    cur.execute('INSERT INTO users (username, password) VALUES (%s, %s) RETURNING id', 
                          (username, generate_password_hash(password)))
    user_id = cur.fetchone()['id']
    conn.commit()
    
    session['user_id'] = user_id
    session['username'] = username
    session['is_admin'] = 0
    cur.close()
    conn.close()
    
    return jsonify({"message": "Registration successful"})

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json
    
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute('SELECT * FROM users WHERE username = %s', (data.get('username'),))
    user = cur.fetchone()
    cur.close()
    conn.close()
    
    if user and check_password_hash(user['password'], data.get('password')):
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
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    cur.execute('SELECT id, title, created_at FROM tests ORDER BY created_at DESC')
    tests = cur.fetchall()
    
    cur.execute('SELECT history FROM users WHERE id = %s', (session['user_id'],))
    user = cur.fetchone()
    
    cur.close()
    conn.close()
    
    return jsonify({
        "tests": [dict(t) for t in tests],
        "history": json.loads(user['history']) if user['history'] else []
    })

@app.route('/api/test/<test_id>', methods=['GET'])
def get_test(test_id):
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401
    
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    cur.execute('SELECT * FROM tests WHERE id = %s', (test_id,))
    test = cur.fetchone()
    
    cur.close()
    conn.close()
    
    if not test: return jsonify({"error": "Test not found"}), 404
    
    questions = json.loads(test['data'])
    safe_questions = [{"id": q["id"], "text": q["text"], "options": q["options"]} for q in questions]
    return jsonify({"title": test['title'], "questions": safe_questions})

@app.route('/api/submit/<test_id>', methods=['POST'])
def submit_test(test_id):
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    cur.execute('SELECT * FROM tests WHERE id = %s', (test_id,))
    test = cur.fetchone()
    
    if not test: 
        cur.close()
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
    
    cur.execute('SELECT history FROM users WHERE id = %s', (session['user_id'],))
    user = cur.fetchone()
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
    
    cur.execute('UPDATE users SET history = %s WHERE id = %s', (json.dumps(history), session['user_id']))
    conn.commit()
    
    cur.close()
    conn.close()

    return jsonify({"score": correct_count, "total": len(questions), "percentage": percentage, 
                    "analysis": analysis, "history": history})

# --- ADMIN API (CRUD) ---
@app.route('/api/admin/data', methods=['GET'])
def admin_data():
    if not session.get('is_admin'): return jsonify({"error": "Unauthorized"}), 403
    
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    cur.execute('SELECT id, title, created_at FROM tests ORDER BY created_at DESC')
    tests = cur.fetchall()
    
    cur.execute('SELECT id, username, history FROM users WHERE is_admin = 0')
    users = cur.fetchall()
    
    cur.close()
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
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    cur.execute('INSERT INTO tests (id, title, data, created_at) VALUES (%s, %s, %s, %s)',
                 (test_id, data['title'], json.dumps(data['questions']), created_at))
    conn.commit()
    
    cur.close()
    conn.close()
    return jsonify({"message": "Test created", "test_id": test_id})

@app.route('/api/admin/tests/<test_id>', methods=['DELETE'])
def delete_test(test_id):
    if not session.get('is_admin'): return jsonify({"error": "Unauthorized"}), 403
    
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    cur.execute('DELETE FROM tests WHERE id = %s', (test_id,))
    conn.commit()
    
    cur.close()
    conn.close()
    return jsonify({"message": "Test deleted"})

@app.route('/api/admin/users', methods=['POST'])
def add_user():
    if not session.get('is_admin'): return jsonify({"error": "Unauthorized"}), 403
    
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        cur.execute('INSERT INTO users (username, password) VALUES (%s, %s)', 
                     (username, generate_password_hash(password)))
        conn.commit()
        return jsonify({"message": "User created successfully"})
    except psycopg2.IntegrityError:
        conn.rollback()
        return jsonify({"error": "Username already exists"}), 400
    finally:
        cur.close()
        conn.close()

if __name__ == '__main__':
    if not DATABASE_URL:
        print("CRITICAL: You must set the DATABASE_URL environment variable to run this app!")
    app.run(debug=True, port=5000)
