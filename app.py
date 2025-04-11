import os
import json
import time
from io import BytesIO
from flask import (
    Flask, request, render_template, redirect, url_for,
    session, flash, send_file, jsonify
)
import g4f
from g4f import ChatCompletion
import PyPDF2
import docx2txt
from concurrent.futures import ThreadPoolExecutor, as_completed
import sys
import asyncio

if sys.platform.startswith('win'):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

app = Flask(__name__)
app.secret_key = 'your-secret-key'  # Replace with a secure secret key in production

# ---------------------------
# JSON Database Functions
# ---------------------------
USERS_DB = "users.json"

def load_users():
    """Load users from a JSON file."""
    if os.path.exists(USERS_DB):
        with open(USERS_DB, "r") as f:
            return json.load(f)
    else:
        return {}

def save_users(users):
    """Save users to a JSON file."""
    with open(USERS_DB, "w") as f:
        json.dump(users, f)

# ---------------------------
# GPT-4O Summarization Function
# ---------------------------
def generate_summary(text, summary_type, summary_length, keywords):
    """
    Generate a summary using GPT-4O.
    
    Parameters:
        text (str): Text to summarize.
        summary_type (str): "Abstractive" or "Extractive".
        summary_length (str): "Short", "Medium", or "Big".
        keywords (str): Optional keywords to focus on.
    Returns:
        str: The generated summary.
    """
    prompt = f"Please provide a {summary_length.lower()} {summary_type.lower()} summary of the following text."
    if keywords.strip():
        prompt += f" Focus on these keywords: {keywords.strip()}."
    prompt += f"\n\nText:\n{text}"

    try:
        response = ChatCompletion.create(
            model="gpt-4o-mini",  # Replace with your desired model
            messages=[{"role": "user", "content": prompt}],
            temperature=0.6,
            top_p=0.9
        )
        return response.strip() if response else "Error: No response from GPT-4O."
    except Exception as e:
        return f"Error: {e}"

# ---------------------------
# File Extraction Functions
# ---------------------------
def extract_text_from_pdf(file):
    """Extract text from a PDF file."""
    try:
        pdf_reader = PyPDF2.PdfReader(file)
        text = ""
        for page in pdf_reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
        return text
    except Exception as e:
        return f"Error reading PDF: {e}"

def extract_text_from_docx(file):
    """Extract text from a DOCX file."""
    try:
        text = docx2txt.process(file)
        return text
    except Exception as e:
        return f"Error reading DOCX: {e}"

def extract_text_from_txt(file):
    """Extract text from a TXT file."""
    try:
        return file.read().decode("utf-8")
    except Exception as e:
        return f"Error reading TXT: {e}"

# ---------------------------
# Routes for Authentication
# ---------------------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        users = load_users()
        if email in users and users[email]["password"] == password:
            session['authenticated'] = True
            session['user_email'] = email
            flash("Logged in successfully!", "success")
            return redirect(url_for('home'))
        else:
            flash("Invalid email or password.", "error")
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        password = request.form.get('password')
        users = load_users()
        if email in users:
            flash("Email already registered. Please login.", "error")
        else:
            users[email] = {"name": name, "password": password}
            save_users(users)
            flash("Registration successful! Please login.", "success")
            return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.clear()
    flash("Logged out successfully!", "success")
    return redirect(url_for('landing'))

# ---------------------------
# Landing Page Route
# ---------------------------
@app.route('/landing')
def landing():
    return render_template('landing.html')

# ---------------------------
# Home and Summarization Routes
# ---------------------------
@app.route('/')
def home():
    if not session.get('authenticated'):
        return redirect(url_for('landing'))
    return render_template('home.html')

@app.route('/summarize', methods=['POST'])
def summarize():
    if not session.get('authenticated'):
        return redirect(url_for('landing'))

    # Get file lists from the three separate file input fields
    pdf_files = request.files.getlist('pdf_files')
    docx_files = request.files.getlist('docx_files')
    txt_files = request.files.getlist('txt_files')
    # Combine all uploaded files into one list
    uploaded_files = pdf_files + docx_files + txt_files

    summary_type = request.form.get('summary_type')
    summary_length = request.form.get('summary_length')
    keywords = request.form.get('keywords', '')

    summaries = {}
    original_texts = {}

    # Create independent file streams for each uploaded file
    files = []
    for uploaded_file in uploaded_files:
        file_bytes = uploaded_file.read()
        file_stream = BytesIO(file_bytes)
        file_stream.filename = uploaded_file.filename  # Attach filename for later use
        file_stream.content_type = uploaded_file.content_type  # Attach content type for later use
        files.append(file_stream)

    # Helper function to process a single file
    def process_file(file_stream):
        filename = file_stream.filename
        content_type = file_stream.content_type
        if content_type == "application/pdf":
            text = extract_text_from_pdf(file_stream)
        elif content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            text = extract_text_from_docx(file_stream)
        elif content_type == "text/plain":
            text = extract_text_from_txt(file_stream)
        else:
            text = "Unsupported file type."
        if text and not text.startswith("Error"):
            summary = generate_summary(text, summary_type, summary_length, keywords)
        else:
            summary = text
        return filename, text, summary

    # Process files concurrently using ThreadPoolExecutor
    with ThreadPoolExecutor() as executor:
        futures = {executor.submit(process_file, file_stream): file_stream for file_stream in files}
        for future in as_completed(futures):
            filename, text, summary = future.result()
            original_texts[filename] = text
            summaries[filename] = summary

    # Render the summaries page with results for all files
    return render_template(
        'summaries.html',
        summaries=summaries,
        original_texts=original_texts,
        summary_type=summary_type,
        summary_length=summary_length,
        keywords=keywords
    )

@app.route('/regenerate', methods=['POST'])
def regenerate():
    if not session.get('authenticated'):
        return redirect(url_for('landing'))

    filename = request.form.get('filename')
    original_text = request.form.get('original_text')
    summary_type = request.form.get('summary_type')
    summary_length = request.form.get('summary_length')
    keywords = request.form.get('keywords', '')

    if original_text and not original_text.startswith("Error"):
        new_summary = generate_summary(original_text, summary_type, summary_length, keywords)
        return render_template('regenerate.html', filename=filename, summary=new_summary)
    else:
        flash("Original text not available for regeneration.", "error")
        return redirect(url_for('home'))

@app.route('/download', methods=['POST'])
def download():
    filename = request.form.get('filename')
    summary = request.form.get('summary')
    buffer = BytesIO()
    buffer.write(summary.encode('utf-8'))
    buffer.seek(0)
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"{filename}_summary.txt",
        mimetype="text/plain"
    )

# ---------------------------
# Chatbot Route for GPT-4O-mini Chat Response
# ---------------------------
@app.route('/chat', methods=['POST'])
def chat():
    if not session.get('authenticated'):
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    user_message = data.get('message', '')
    if not user_message:
        return jsonify({'error': 'No message provided'}), 400

    try:
        response = ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": user_message}],
            temperature=0.6,
            top_p=0.9
        )
        chat_response = response.strip() if response else "Error: No response from GPT-4O."
    except Exception as e:
        chat_response = f"Error: {e}"
    return jsonify({'response': chat_response})

# ---------------------------
# Feedback Route
# ---------------------------
@app.route('/feedback', methods=['POST'])
def feedback():
    if not session.get('authenticated'):
        return redirect(url_for('landing'))
    feedback_text = request.form.get('feedback')
    feedback_line = f"User: {session.get('user_email', 'unknown')} | Feedback: {feedback_text}\n"
    with open("feedback.txt", "a") as f:
        f.write(feedback_line)
    flash("Thank you for your feedback!", "success")
    return redirect(url_for('home'))

# ---------------------------
# Run the Flask App
# ---------------------------
if __name__ == '__main__':
    app.run(debug=True)

