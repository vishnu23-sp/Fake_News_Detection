"""
TruthLens Backend — app.py  v4.1
=================================
Local Ollama (Llama 3.1:8b) + Tavily web search for real-time fact checking.
Supports voice/speech input type tracking from the frontend.
Native language support: Tamil, Telugu, Hindi, English.
  - User selects output language from the UI dropdown
  - If 'auto', language is detected from the input text
  - Otherwise the selected language is used for all output regardless of input

Setup:
  pip install flask flask-cors flask-mail requests beautifulsoup4
              mysql-connector-python bcrypt google-auth tavily-python python-dotenv
  python app.py   (Ollama must be running in background)
"""

from flask import Flask, request, jsonify, render_template, session, redirect, url_for
from flask_cors import CORS
from flask_mail import Mail, Message
from tavily import TavilyClient
from bs4 import BeautifulSoup
import mysql.connector
import bcrypt
import requests
import re
import os
import json
import secrets
from datetime import datetime, timedelta
from functools import wraps
from dotenv import load_dotenv

# Google OAuth
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

# ─── Load environment variables from .env ──────────────────────────────────
load_dotenv()

app = Flask(__name__)

# ─── Secret key ────────────────────────────────────────────────────────────
app.secret_key = os.environ.get("SECRET_KEY")
app.config["SESSION_COOKIE_HTTPONLY"]    = True
app.config["SESSION_COOKIE_SAMESITE"]   = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)

CORS(app, supports_credentials=True, origins=["http://localhost:5000", "http://127.0.0.1:5000"])

# ─── Flask-Mail Config ─────────────────────────────────────────────────────
app.config["MAIL_SERVER"]         = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
app.config["MAIL_PORT"]           = int(os.environ.get("MAIL_PORT", 587))
app.config["MAIL_USE_TLS"]        = True
app.config["MAIL_USERNAME"]       = os.environ.get("MAIL_USERNAME")
app.config["MAIL_PASSWORD"]       = os.environ.get("MAIL_PASSWORD")
app.config["MAIL_DEFAULT_SENDER"] = os.environ.get("MAIL_USERNAME")
mail = Mail(app)

# ─── Google OAuth Config ───────────────────────────────────────────────────
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")

# ─── Ollama + Tavily Config ────────────────────────────────────────────────
OLLAMA_URL     = "http://localhost:11434/api/generate"
MODEL          = "llama3.1:8b"
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY")
tavily         = TavilyClient(api_key=TAVILY_API_KEY)

# ─── MySQL Config ──────────────────────────────────────────────────────────
DB_CONFIG = {
    "host":     os.environ.get("DB_HOST", "localhost"),
    "user":     os.environ.get("DB_USER", "root"),
    "password": os.environ.get("DB_PASS"),
    "database": "truthlens"
}

def get_db():
    return mysql.connector.connect(**DB_CONFIG)

# ─── Init DB ───────────────────────────────────────────────────────────────
def init_db():
    conn = mysql.connector.connect(
        host=DB_CONFIG["host"],
        user=DB_CONFIG["user"],
        password=DB_CONFIG["password"]
    )
    cursor = conn.cursor()
    cursor.execute("CREATE DATABASE IF NOT EXISTS truthlens")
    cursor.execute("USE truthlens")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            email VARCHAR(150) UNIQUE NOT NULL,
            password_hash VARCHAR(255) DEFAULT NULL,
            google_id VARCHAR(255) DEFAULT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # chat_history: input_type accepts 'claim' | 'article' | 'voice'
    # language column stores detected/selected language
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_history (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL,
            input_text TEXT NOT NULL,
            input_type VARCHAR(20) NOT NULL DEFAULT 'claim',
            language VARCHAR(20) NOT NULL DEFAULT 'English',
            verdict VARCHAR(20) NOT NULL,
            explanation TEXT NOT NULL,
            corrected_statement TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    # Migrate: add input_type column if it doesn't exist (for existing DBs)
    try:
        cursor.execute("ALTER TABLE chat_history MODIFY COLUMN input_type VARCHAR(20) NOT NULL DEFAULT 'claim'")
        conn.commit()
    except Exception:
        pass

    # Migrate: add language column if it doesn't exist (for existing DBs)
    try:
        cursor.execute("ALTER TABLE chat_history ADD COLUMN language VARCHAR(20) NOT NULL DEFAULT 'English'")
        conn.commit()
    except Exception:
        pass  # Column already exists

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS password_resets (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL,
            token VARCHAR(100) NOT NULL UNIQUE,
            expires_at DATETIME NOT NULL,
            used TINYINT(1) DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    conn.commit()
    cursor.close()
    conn.close()
    print("✅ Database initialized")


# ─── Password validation ───────────────────────────────────────────────────
def validate_password(password):
    errors = []
    if len(password) < 8:
        errors.append("at least 8 characters")
    if not re.search(r"[A-Z]", password):
        errors.append("at least 1 uppercase letter (A-Z)")
    if not re.search(r"[a-z]", password):
        errors.append("at least 1 lowercase letter (a-z)")
    if not re.search(r"[0-9]", password):
        errors.append("at least 1 number (0-9)")
    if not re.search(r"[!@#$%^&*()\-_=+\[\]{};:'\",.<>/?\\|`~]", password):
        errors.append("at least 1 special character (!@#$%...)")
    return errors


# ─── Auth decorator ────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "Unauthorized — please log in"}), 401
        return f(*args, **kwargs)
    return decorated


# ══════════════════════════════════════════════════════════════════════════════
#  LANGUAGE DETECTION  (Unicode block ranges — no extra pip installs)
# ══════════════════════════════════════════════════════════════════════════════

def detect_language(text: str) -> str:
    """
    Detect if input is Tamil, Telugu, Hindi (Devanagari), or English.
    Uses Unicode block ranges — works for typed and voice-transcribed text.

    Unicode ranges:
      Tamil      : U+0B80 – U+0BFF
      Telugu     : U+0C00 – U+0C7F
      Hindi (Dev): U+0900 – U+097F

    Returns one of: 'Tamil', 'Telugu', 'Hindi', 'English'
    """
    tamil_count  = sum(1 for ch in text if 0x0B80 <= ord(ch) <= 0x0BFF)
    telugu_count = sum(1 for ch in text if 0x0C00 <= ord(ch) <= 0x0C7F)
    hindi_count  = sum(1 for ch in text if 0x0900 <= ord(ch) <= 0x097F)

    max_count = max(tamil_count, telugu_count, hindi_count)

    if max_count == 0:
        return "English"
    if tamil_count == max_count:
        return "Tamil"
    if telugu_count == max_count:
        return "Telugu"
    return "Hindi"


# ══════════════════════════════════════════════════════════════════════════════
#  CORE ANALYSIS ENGINE  (Tavily + Ollama)
# ══════════════════════════════════════════════════════════════════════════════

def scrape_url(url: str) -> str:
    """Scrape article text content from a URL."""
    try:
        headers  = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        response = requests.get(url, headers=headers, timeout=10)
        soup     = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        title      = soup.find("title")
        title_text = title.get_text(strip=True) if title else ""
        body       = " ".join(p.get_text(strip=True) for p in soup.find_all("p")[:30])
        return f"Title: {title_text}\n\n{body}"
    except Exception as e:
        return f"Could not scrape URL: {str(e)}"


def search_tavily(query: str) -> str:
    """
    Search Tavily for real-time web evidence.
    Works for text claims, voice transcripts, and article URLs.
    Returns clean context string for the LLM prompt.
    """
    try:
        print(f"[TruthLens] 🔍 Tavily search: {query[:80]}...")
        response = tavily.search(
            query          = query,
            search_depth   = "advanced",
            max_results    = 5,
            include_answer = True,
        )
        parts = []
        if response.get("answer"):
            parts.append(f"SEARCH SUMMARY: {response['answer']}")

        results = response.get("results", [])
        if results:
            parts.append("\nSOURCES FOUND:")
            for i, r in enumerate(results, 1):
                title   = r.get("title", "Unknown")
                url     = r.get("url", "")
                content = r.get("content", "")[:350]
                parts.append(f"\n[{i}] {title}\n    URL: {url}\n    {content}")

        print(f"[TruthLens] ✅ Tavily: {len(results)} results")
        return "\n".join(parts) if parts else "No relevant results found."

    except Exception as e:
        print(f"[TruthLens] ⚠️  Tavily failed: {e}")
        return "Web search unavailable. Analyze based on content only."


def run_ollama(prompt: str) -> str:
    """Send prompt to local Ollama Llama 3.1:8b and return response text."""
    payload = {
        "model"  : MODEL,
        "prompt" : prompt,
        "stream" : False,
        "options": {
            "temperature": 0.1,
            "num_predict": 700,
        }
    }
    print(f"[TruthLens] 🤖 Sending to Ollama ({MODEL})...")
    resp = requests.post(OLLAMA_URL, json=payload, timeout=180)
    resp.raise_for_status()
    raw = resp.json().get("response", "").strip()
    print(f"[TruthLens] ✅ Ollama responded ({len(raw)} chars)")
    return raw


def build_prompt(input_type: str, content: str, search_context: str, language: str = "English") -> str:
    """
    Build enriched prompt with content + Tavily real-time evidence.

    input_type : 'claim' | 'article' | 'voice'
    language   : 'English' | 'Tamil' | 'Telugu' | 'Hindi'

    When language is not English, the model is explicitly instructed to
    write EXPLANATION and CORRECTED_STATEMENT in that native language.
    The structural label words (VERDICT:, EXPLANATION:, CORRECTED_STATEMENT:)
    remain in English so that parse_llm_response() can reliably extract them.
    """
    today        = datetime.now().strftime("%B %d, %Y")
    display_type = "voice-transcribed claim" if input_type == "voice" else input_type

    # Language instruction injected only for non-English inputs
    if language != "English":
        language_instruction = f"""

LANGUAGE INSTRUCTION (MANDATORY):
- The user has selected {language} as their output language.
- You MUST respond entirely in {language}.
- Write the EXPLANATION and CORRECTED_STATEMENT sections fully in {language}.
- Do NOT use English words in those sections (except proper nouns, technical terms, or source names that have no {language} equivalent).
- The label words VERDICT:, EXPLANATION:, CORRECTED_STATEMENT: must stay exactly as shown so the system can parse your reply — do not translate those labels.
- The value after VERDICT: must be one of: TRUE, FALSE, UNCERTAIN (always in English, uppercase).
"""
    else:
        language_instruction = ""

    return f"""You are an expert fact-checker and investigative journalist. Today is {today}.
{language_instruction}
You have real-time web search results to help you verify the following {display_type}.
READ ALL EVIDENCE CAREFULLY before giving your verdict.

=== {'ARTICLE CONTENT' if input_type == 'article' else 'CLAIM TO VERIFY'} ===
{content[:3000]}

=== REAL-TIME WEB SEARCH EVIDENCE ===
{search_context}

=== RULES ===
- Web evidence CONFIRMS the content  →  verdict: TRUE
- Web evidence CONTRADICTS content   →  verdict: FALSE
- Evidence mixed or insufficient     →  verdict: UNCERTAIN
- NEVER say FALSE just because you did not know it — always trust the search evidence
- For ARTICLES: judge whether the main claims in the article are factually accurate
- For TEXT/VOICE CLAIMS: judge whether the specific statement is correct

=== RESPONSE — use EXACTLY this format, nothing else ===

VERDICT: [TRUE or FALSE or UNCERTAIN]
EXPLANATION: [2-3 sentences explaining your verdict{'  in ' + language if language != 'English' else ''}. Mention specific evidence from the web search that led to this conclusion.]
CORRECTED_STATEMENT: [
  If TRUE     → write: "{'தகவல் சரியாக உள்ளது.' if language == 'Tamil' else 'సమాచారం సరిగ్గా ఉంది.' if language == 'Telugu' else 'जानकारी सही है।' if language == 'Hindi' else 'The information is accurate as reported.'}"
  If FALSE    → write the corrected, factually accurate version{'  in ' + language if language != 'English' else ''}.
  If UNCERTAIN→ write what is confirmed and what remains unverified{'  in ' + language if language != 'English' else ''}.
]"""


def parse_llm_response(raw: str) -> dict:
    """Parse Llama's structured text response into a Python dict."""
    verdict_match     = re.search(r"VERDICT:\s*(TRUE|FALSE|UNCERTAIN)", raw, re.IGNORECASE)
    explanation_match = re.search(r"EXPLANATION:\s*(.+?)(?=CORRECTED_STATEMENT:|$)", raw, re.IGNORECASE | re.DOTALL)
    corrected_match   = re.search(r"CORRECTED_STATEMENT:\s*(.+?)$", raw, re.IGNORECASE | re.DOTALL)

    return {
        "verdict":             verdict_match.group(1).upper() if verdict_match else "UNCERTAIN",
        "explanation":         explanation_match.group(1).strip() if explanation_match else raw.strip(),
        "corrected_statement": corrected_match.group(1).strip() if corrected_match else "Unable to generate corrected statement.",
    }


def analyze_claim(user_input: str, input_type_override: str = None, force_language: str = None) -> dict:
    """
    Full pipeline:
      1. Detect input type: URL → article, otherwise claim or voice
      2. Determine output language:
         - If force_language is provided (user selected from UI dropdown), use it.
         - Otherwise auto-detect from input text.
      3. Scrape URL content (if article)
      4. Tavily web search for real-time evidence
      5. Build enriched prompt with native-language instruction
      6. Run local Llama 3.1:8b via Ollama
      7. Parse and return result dict (includes 'language' field)

    input_type_override : pass 'voice' when the text came from speech recognition
    force_language      : 'English' | 'Tamil' | 'Telugu' | 'Hindi' — set by UI language selector.
                          When provided, overrides auto-detection so the output language
                          always matches what the user explicitly chose.
    """
    is_url = user_input.strip().startswith("http://") or user_input.strip().startswith("https://")

    if is_url:
        input_type = "article"
    elif input_type_override == "voice":
        input_type = "voice"
    else:
        input_type = "claim"

    # ── Step 1: Determine output language ─────────────────────────────────
    # Priority: explicit UI selection > auto-detect from text
    # For URLs the article content is English, but if the user explicitly
    # chose a non-English language we still honour it.
    if force_language and force_language in ("English", "Tamil", "Telugu", "Hindi"):
        language = force_language
        print(f"[TruthLens] 🌐 Output language (user selected): {language}")
    else:
        language = "English" if is_url else detect_language(user_input)
        print(f"[TruthLens] 🌐 Output language (auto-detected): {language}")

    # ── Step 2: Get content to analyse ────────────────────────────────────
    if is_url:
        print(f"[TruthLens] 🌐 Scraping article URL...")
        content      = scrape_url(user_input)
        search_query = user_input
    else:
        content      = user_input
        search_query = user_input

    # ── Step 3: Real-time web evidence via Tavily ──────────────────────────
    search_context = search_tavily(search_query)

    # ── Step 4: Build prompt with language instruction ─────────────────────
    prompt = build_prompt(input_type, content, search_context, language=language)

    # ── Step 5: Local LLM inference ───────────────────────────────────────
    raw_response = run_ollama(prompt)

    # ── Step 6: Parse response ────────────────────────────────────────────
    result               = parse_llm_response(raw_response)
    result["input_type"] = input_type
    result["language"]   = language   # returned to frontend for display / TTS locale

    print(f"[TruthLens] 📋 Input type: {input_type} | Language: {language} | Verdict: {result['verdict']}")

    return result


# ══════════════════════════════════════════════════════════════════════════════
#  PAGE ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    if "user_id" not in session:
        return redirect(url_for("login_page"))
    return render_template("index.html")

@app.route("/login")
def login_page():
    if "user_id" in session:
        return redirect(url_for("index"))
    return render_template("login.html")

@app.route("/forgot-password")
def forgot_password_page():
    return render_template("forgot_password.html")

@app.route("/reset-password/<token>")
def reset_password_page(token):
    try:
        conn   = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT pr.*, u.email FROM password_resets pr
            JOIN users u ON pr.user_id = u.id
            WHERE pr.token = %s AND pr.used = 0 AND pr.expires_at > NOW()
        """, (token,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
    except Exception:
        return render_template("reset_password.html", valid=False, token=token)
    if not row:
        return render_template("reset_password.html", valid=False, token=token)
    return render_template("reset_password.html", valid=True, token=token, email=row["email"])


# ══════════════════════════════════════════════════════════════════════════════
#  AUTH API
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid request body"}), 400
    name     = data.get("name", "").strip()
    email    = data.get("email", "").strip().lower()
    password = data.get("password", "")
    if not name or not email or not password:
        return jsonify({"error": "All fields are required"}), 400
    if len(name) < 2:
        return jsonify({"error": "Name must be at least 2 characters"}), 400
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return jsonify({"error": "Please enter a valid email address"}), 400
    pw_errors = validate_password(password)
    if pw_errors:
        return jsonify({"error": "Password must contain: " + ", ".join(pw_errors)}), 400
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    try:
        conn   = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO users (name, email, password_hash) VALUES (%s, %s, %s)",
            (name, email, password_hash)
        )
        conn.commit()
        user_id = cursor.lastrowid
        cursor.close()
        conn.close()
        session.permanent     = True
        session["user_id"]    = user_id
        session["user_name"]  = name
        session["user_email"] = email
        return jsonify({"message": "Account created", "user_id": user_id, "name": name, "email": email})
    except mysql.connector.IntegrityError:
        return jsonify({"error": "That email is already registered"}), 409
    except Exception as e:
        return jsonify({"error": f"Database error: {str(e)}"}), 500


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid request body"}), 400
    email    = data.get("email", "").strip().lower()
    password = data.get("password", "")
    if not email or not password:
        return jsonify({"error": "Email and password are required"}), 400
    try:
        conn   = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()
        cursor.close()
        conn.close()
    except Exception as e:
        return jsonify({"error": f"Database error: {str(e)}"}), 500
    if not user:
        return jsonify({"error": "Invalid email or password"}), 401
    if not user.get("password_hash"):
        return jsonify({"error": "This account uses Google Sign-In. Please continue with Google."}), 401
    if not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
        return jsonify({"error": "Invalid email or password"}), 401
    session.permanent     = True
    session["user_id"]    = user["id"]
    session["user_name"]  = user["name"]
    session["user_email"] = user["email"]
    return jsonify({"message": "Login successful", "user_id": user["id"], "name": user["name"], "email": user["email"]})


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"message": "Logged out"})


@app.route("/api/me")
@login_required
def me():
    return jsonify({
        "user_id": session["user_id"],
        "name":    session["user_name"],
        "email":   session["user_email"]
    })


@app.route("/api/forgot-password", methods=["POST"])
def forgot_password():
    data  = request.get_json(silent=True)
    email = (data.get("email", "") if data else "").strip().lower()
    if not email or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return jsonify({"error": "Please provide a valid email address"}), 400
    try:
        conn   = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, name FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()
        if not user:
            cursor.close()
            conn.close()
            return jsonify({"message": "If that email is registered, you'll receive a reset link shortly."})
        cursor2    = conn.cursor()
        cursor2.execute("UPDATE password_resets SET used = 1 WHERE user_id = %s AND used = 0", (user["id"],))
        token      = secrets.token_hex(32)
        expires_at = datetime.now() + timedelta(hours=1)
        cursor2.execute(
            "INSERT INTO password_resets (user_id, token, expires_at) VALUES (%s, %s, %s)",
            (user["id"], token, expires_at)
        )
        conn.commit()
        cursor.close()
        cursor2.close()
        conn.close()
        reset_url = f"http://localhost:5000/reset-password/{token}"
        msg       = Message(subject="TruthLens — Reset your password", recipients=[email])
        msg.body  = f"Hi {user['name']},\n\nReset link: {reset_url}\n\nValid for 1 hour.\n\n— TruthLens Team"
        msg.html  = f"""
<div style="font-family:sans-serif;max-width:480px;margin:0 auto;color:#1a1a1a">
  <h2>Reset your password</h2>
  <p>Hi {user['name']}, click below to set a new password (expires in 1 hour).</p>
  <a href="{reset_url}" style="display:inline-block;background:#0a0a0a;color:#f5f2eb;text-decoration:none;padding:12px 28px;font-size:14px;border-radius:2px">RESET PASSWORD</a>
  <p style="color:#999;font-size:12px;margin-top:24px">If you didn't request this, ignore this email.</p>
</div>"""
        mail.send(msg)
    except Exception as e:
        return jsonify({"error": f"Failed to send email: {str(e)}"}), 500
    return jsonify({"message": "If that email is registered, you'll receive a reset link shortly."})


@app.route("/api/reset-password", methods=["POST"])
def reset_password():
    data     = request.get_json(silent=True)
    token    = (data.get("token", "") if data else "").strip()
    password = (data.get("password", "") if data else "")
    if not token or not password:
        return jsonify({"error": "Token and new password are required"}), 400
    pw_errors = validate_password(password)
    if pw_errors:
        return jsonify({"error": "Password must contain: " + ", ".join(pw_errors)}), 400
    try:
        conn   = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT pr.id, pr.user_id FROM password_resets pr
            WHERE pr.token = %s AND pr.used = 0 AND pr.expires_at > NOW()
        """, (token,))
        row = cursor.fetchone()
        if not row:
            cursor.close()
            conn.close()
            return jsonify({"error": "This reset link is invalid or has expired."}), 400
        new_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        cursor2  = conn.cursor()
        cursor2.execute("UPDATE users SET password_hash = %s WHERE id = %s", (new_hash, row["user_id"]))
        cursor2.execute("UPDATE password_resets SET used = 1 WHERE id = %s", (row["id"],))
        conn.commit()
        cursor.close()
        cursor2.close()
        conn.close()
    except Exception as e:
        return jsonify({"error": f"Database error: {str(e)}"}), 500
    return jsonify({"message": "Password updated successfully. You can now sign in."})


@app.route("/api/auth/google", methods=["POST"])
def google_auth():
    data         = request.get_json(silent=True)
    id_token_str = (data.get("credential", "") if data else "").strip()
    if not id_token_str:
        return jsonify({"error": "No Google credential provided"}), 400
    try:
        idinfo = id_token.verify_oauth2_token(
            id_token_str, google_requests.Request(), GOOGLE_CLIENT_ID
        )
    except ValueError as e:
        return jsonify({"error": f"Invalid Google token: {str(e)}"}), 401
    google_id = idinfo["sub"]
    email     = idinfo.get("email", "").lower()
    name      = idinfo.get("name") or email.split("@")[0]
    if not email:
        return jsonify({"error": "Could not retrieve email from Google"}), 400
    try:
        conn    = get_db()
        cursor  = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE google_id = %s OR email = %s", (google_id, email))
        user    = cursor.fetchone()
        cursor2 = conn.cursor()
        if user:
            if not user.get("google_id"):
                cursor2.execute("UPDATE users SET google_id = %s WHERE id = %s", (google_id, user["id"]))
                conn.commit()
            user_id   = user["id"]
            user_name = user["name"]
        else:
            cursor2.execute(
                "INSERT INTO users (name, email, google_id) VALUES (%s, %s, %s)",
                (name, email, google_id)
            )
            conn.commit()
            user_id   = cursor2.lastrowid
            user_name = name
        cursor.close()
        cursor2.close()
        conn.close()
    except Exception as e:
        return jsonify({"error": f"Database error: {str(e)}"}), 500
    session.permanent     = True
    session["user_id"]    = user_id
    session["user_name"]  = user_name
    session["user_email"] = email
    return jsonify({"message": "Signed in with Google", "user_id": user_id, "name": user_name, "email": email})


# ══════════════════════════════════════════════════════════════════════════════
#  ANALYZE ROUTE — Ollama + Tavily  (handles voice + native language output)
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/analyze", methods=["POST"])
@login_required
def analyze():
    data = request.get_json(silent=True)
    if not data or "input" not in data:
        return jsonify({"error": "No input provided"}), 400

    user_input = data["input"].strip()
    if not user_input:
        return jsonify({"error": "Input cannot be empty"}), 400

    # Frontend sends 'voice' when text came from speech recognition
    input_type_override = data.get("input_type_override", None)
    # Sanitize: only allow 'voice' as a valid override
    if input_type_override not in ("voice", None):
        input_type_override = None

    # ── NEW: explicit output language chosen by the user in the UI ──────────
    # Values: 'English' | 'Tamil' | 'Telugu' | 'Hindi' | None (auto-detect)
    force_language = data.get("force_language", None)
    valid_languages = ("English", "Tamil", "Telugu", "Hindi")
    if force_language not in valid_languages:
        force_language = None  # fall back to auto-detect

    try:
        result = analyze_claim(
            user_input,
            input_type_override=input_type_override,
            force_language=force_language
        )

        conn   = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO chat_history
            (user_id, input_text, input_type, language, verdict, explanation, corrected_statement)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            session["user_id"],
            user_input[:1000],
            result["input_type"],
            result.get("language", "English"),
            result["verdict"],
            result["explanation"],
            result["corrected_statement"]
        ))
        conn.commit()
        result["history_id"] = cursor.lastrowid
        cursor.close()
        conn.close()

        return jsonify(result)

    except requests.exceptions.ConnectionError:
        return jsonify({"error": "Cannot connect to Ollama. Make sure it is running — check Task Manager."}), 503
    except requests.exceptions.Timeout:
        return jsonify({"error": "Ollama timed out (3 min). The model is busy — try again shortly."}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
#  HISTORY ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/history")
@login_required
def history():
    try:
        conn   = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, input_text, input_type, language, verdict, explanation,
                   corrected_statement, created_at
            FROM chat_history
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT 50
        """, (session["user_id"],))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        for row in rows:
            row["created_at"] = row["created_at"].strftime("%d %b %Y, %I:%M %p")
        return jsonify(rows)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/history/<int:history_id>", methods=["DELETE"])
@login_required
def delete_history(history_id):
    try:
        conn   = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM chat_history WHERE id = %s AND user_id = %s",
            (history_id, session["user_id"])
        )
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({"message": "Deleted"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
#  EXTENSION API — Ollama + Tavily (supports voice + native language)
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/extension/analyze", methods=["POST"])
def extension_analyze():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return jsonify({"error": "Unauthorized"}), 401
    token = auth[7:]
    try:
        conn   = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE id = %s", (int(token),))
        user = cursor.fetchone()
        cursor.close()
        conn.close()
    except Exception as e:
        return jsonify({"error": f"Auth error: {str(e)}"}), 500
    if not user:
        return jsonify({"error": "Invalid token — please sign in again"}), 401

    data       = request.get_json(silent=True)
    user_input = data.get("input", "").strip() if data else ""
    if not user_input:
        return jsonify({"error": "No input provided"}), 400

    input_type_override = data.get("input_type_override", None) if data else None
    if input_type_override not in ("voice", None):
        input_type_override = None

    force_language = data.get("force_language", None) if data else None
    if force_language not in ("English", "Tamil", "Telugu", "Hindi"):
        force_language = None

    try:
        result = analyze_claim(
            user_input,
            input_type_override=input_type_override,
            force_language=force_language
        )
        conn   = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO chat_history
            (user_id, input_text, input_type, language, verdict, explanation, corrected_statement)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            user["id"],
            user_input[:1000],
            result["input_type"],
            result.get("language", "English"),
            result["verdict"],
            result["explanation"],
            result["corrected_statement"]
        ))
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Health check ──────────────────────────────────────────────────────────
@app.route("/health", methods=["GET"])
def health():
    ollama_ok = False
    try:
        r = requests.get("http://localhost:11434", timeout=3)
        ollama_ok = r.status_code == 200
    except Exception:
        pass
    return jsonify({
        "status"          : "ok",
        "version"         : "4.1",
        "model"           : f"{MODEL} (local Ollama)",
        "ollama_running"  : ollama_ok,
        "web_search"      : "Tavily (free — 1000 searches/month)",
        "speech_input"    : "Web Speech API (browser-side, no backend changes needed)",
        "tts_output"      : "Web Speech Synthesis API (browser-side)",
        "language_support": ["English", "Tamil", "Telugu", "Hindi"],
        "language_select" : "UI dropdown overrides auto-detection",
        "language_detect" : "Unicode block ranges (no extra pip install needed)",
        "cost"            : "FREE"
    })


# ─── Start ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000, host="localhost")