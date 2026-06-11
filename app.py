from flask import Flask, render_template, jsonify, request, session, redirect, url_for
import os
from functools import wraps

app = Flask(__name__)

# Get authentication credentials from environment variables
USERNAME = os.environ.get('APP_USERNAME', 'user')
PASSWORD = os.environ.get('APP_PASSWORD', 'password')
app.secret_key = os.environ.get('SECRET_KEY', 'your-secret-key-change-this-in-production')

def login_required(f):
    """Decorator to require authentication for routes"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('authenticated'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page"""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if username == USERNAME and password == PASSWORD:
            session['authenticated'] = True
            next_page = request.args.get('next')
            return redirect(next_page) if next_page else redirect(url_for('index'))
        else:
            return render_template('login.html', error='Invalid username or password')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    """Logout and clear session"""
    session.pop('authenticated', None)
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    """Main page"""
    return render_template('index.html')

@app.route('/api/data')
@login_required
def api_data():
    """Sample API endpoint"""
    return jsonify({
        'message': 'Hello from the API!',
        'status': 'success'
    })

@app.route('/health')
def health_check():
    """Health check endpoint"""
    return jsonify({'status': 'healthy'})

if __name__ == '__main__':
    # For development only - use gunicorn in production
    app.run(host='0.0.0.0', port=5000, debug=False)
