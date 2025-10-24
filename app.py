import os
import secrets
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import psycopg2
from psycopg2.extras import RealDictCursor
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS
import sys
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", secrets.token_hex(16))
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
app.config['ALLOWED_MIMES'] = {'image/png', 'image/jpeg', 'image/gif', 'image/webp'}

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

def allowed_file(filename):
    return '.' in filename and \
    filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def validate_image(file):
    if not file or not file.filename:
        return False
    
    if not allowed_file(file.filename):
        return False
    
    if file.content_type not in app.config['ALLOWED_MIMES']:
        return False
    
    try:
        img = Image.open(file.stream)
        img.verify()
        file.stream.seek(0)
        return True
    except Exception:
        return False
    
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'agency_login'

# Database connection
def get_db():
    username= os.environ.get('DB_USER')
    password = os.environ.get('DB_PASSWORD')
    url = os.environ.get('DB_HOST')
    db_name = os.environ.get('DB_NAME')
    conn = psycopg2.connect(database=db_name, user=username, password=password)
    return conn

# Initialize database tables
def init_db():
    conn = get_db()
    cur = conn.cursor()
    
    # Create agencies table
    cur.execute('''
        CREATE TABLE IF NOT EXISTS agencies (
            id SERIAL PRIMARY KEY,
            name VARCHAR(255) NOT NULL UNIQUE,
            email VARCHAR(255) NOT NULL UNIQUE,
            password_hash VARCHAR(255) NOT NULL,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Create reports table
    cur.execute('''
        CREATE TABLE IF NOT EXISTS reports (
            id SERIAL PRIMARY KEY,
            agency_id INTEGER REFERENCES agencies(id),
            message TEXT NOT NULL,
            image_path VARCHAR(500),
            latitude DECIMAL(10, 8),
            longitude DECIMAL(11, 8),
            location_method VARCHAR(50),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    cur.close()
    conn.close()

class Agency(UserMixin):
    def __init__(self, id, name, email, description):
        self.id = id
        self.name = name
        self.email = email
        self.description = description

@login_manager.user_loader
def load_user(user_id):
    conn = get_db()
    curr = conn.cursor(cursor_factory=RealDictCursor)
    curr.execute("SELECT * FROM Agencies WHERE id = %s", (user_id))
    agency_data = curr.fetchone()
    curr.close()
    curr.close()

    if agency_data:
        return Agency(agency_data['id'], agency_data['name'], agency_data['email'], agency_data['description'])
    return None

def get_exif_gps(image_path):
    try:
        image = Image.open(image_path)
        exifdata = image.getexif()
        
        if not exifdata:
            return None
        
        gps_info = {}
        for tag_id, value in exifdata.items():
            tag = TAGS.get(tag_id, tag_id)
            if tag == "GPSInfo":
                for gps_tag in value:
                    sub_tag = GPSTAGS.get(gps_tag, gps_tag)
                    gps_info[sub_tag] = value[gps_tag]
        
        if not gps_info:
            return None
        
        # Convert GPS coordinates to decimal
        def convert_to_degrees(value):
            d, m, s = value
            # Handle EXIF rational tuples (numerator, denominator)
            d = float(d[0]) / float(d[1]) if isinstance(d, tuple) else float(d)
            m = float(m[0]) / float(m[1]) if isinstance(m, tuple) else float(m)
            s = float(s[0]) / float(s[1]) if isinstance(s, tuple) else float(s)
            return d + (m / 60.0) + (s / 3600.0)
        
        lat = gps_info.get('GPSLatitude')
        lat_ref = gps_info.get('GPSLatitudeRef')
        lon = gps_info.get('GPSLongitude')
        lon_ref = gps_info.get('GPSLongitudeRef')
        
        if lat and lon and lat_ref and lon_ref:
            latitude = convert_to_degrees(lat)
            if lat_ref == 'S':
                latitude = -latitude
            
            longitude = convert_to_degrees(lon)
            if lon_ref == 'W':
                longitude = -longitude
            
            return {'latitude': latitude, 'longitude': longitude}
    except Exception as e:
        print(f"Error extracting GPS data: {e}")
    
    return None

@app.route('/')
def index():
    conn = get_db()
    curr = conn.cursor(cursor_factory=RealDictCursor)
    curr.execute('SELECT id, name, description FROM agencies ORDER BY name')
    agencies = curr.fetchall()
    curr.close()
    conn.close()

    return render_template('index.html', agencies=agencies)

@app.route('/report/<int:agency_id>', methods=['GET', 'POST'])
def submit_report(agency_id):
    """Submit a report to a specific agency"""
    if request.method == 'POST':
        message = request.form.get('message')
        latitude = request.form.get('latitude')
        longitude = request.form.get('longitude')
        location_method = request.form.get('location_method', 'manual')
        
        if not message:
            flash('Please provide a message', 'error')
            return redirect(url_for('submit_report', agency_id=agency_id))
        
        # Handle file upload
        image_path = None
        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename:
                # Validate file is a safe image
                if not validate_image(file):
                    flash('Invalid image file. Please upload a valid image (PNG, JPG, GIF, or WebP).', 'error')
                    return redirect(url_for('submit_report', agency_id=agency_id))
                
                filename = secure_filename(f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{file.filename}")
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(filepath)
                image_path = filename
                
                # Try to extract GPS from image if no manual location provided
                if not latitude or not longitude:
                    gps_data = get_exif_gps(filepath)
                    if gps_data:
                        latitude = gps_data['latitude']
                        longitude = gps_data['longitude']
                        location_method = 'exif'
        
        # Convert latitude and longitude to float
        try:
            latitude = float(latitude) if latitude else None
            longitude = float(longitude) if longitude else None
        except ValueError:
            latitude = None
            longitude = None
        
        # Save report to database
        conn = get_db()
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO reports (agency_id, message, image_path, latitude, longitude, location_method)
            VALUES (%s, %s, %s, %s, %s, %s)
        ''', (agency_id, message, image_path, latitude, longitude, location_method))
        conn.commit()
        cur.close()
        conn.close()
        
        flash('Report submitted successfully! Thank you for helping improve our community.', 'success')
        return redirect(url_for('index'))
    
    # GET request - show form
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('SELECT * FROM agencies WHERE id = %s', (agency_id,))
    agency = cur.fetchone()
    cur.close()
    conn.close()
    
    if not agency:
        flash('Agency not found', 'error')
        return redirect(url_for('index'))
    
    return render_template('submit_report.html', agency=agency)

@app.route('/agency/register', methods=['GET', 'POST'])
def agency_register():
    if request.method == 'POST':
        password = request.form.get('password')
        name = request.form.get('name')
        email = request.form.get('email')
        description = request.form.get('description')

        if not name or not email or not password:
            flash('All fields are required', 'error')
            return redirect(url_for('agency_register'))
        
        conn = get_db()
        curr = conn.cursor(cursor_factory=RealDictCursor)
        curr.execute('SELECT * FROM agencies WHERE email = %s OR name = %s', (name, email))
        existing = curr.fetchone()

        if existing:
            flash('Agency with this name or email already exists', 'error')
            curr.close()
            conn.close()
            return redirect(url_for('agency_register'))
        
        password_hash = generate_password_hash(password)
        curr.execute('''
            INSERT INTO agencies (name, email, password_hash, description)
            VALUES (%s, %s, %s, %s)
        ''', (name, email, password_hash, description))
        conn.commit()
        curr.close()
        conn.close()

        flash('Agency registered successfully! Please log in.', 'success')
        return redirect(url_for('agency_login'))
    
    return render_template('agency_register.html')

@app.route('/agency/login', methods=['GET', 'POST'])
def agency_login():
    """Agency login"""
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute('SELECT * FROM agencies WHERE email = %s', (email,))
        agency_data = cur.fetchone()
        cur.close()
        conn.close()
        
        if agency_data and password and check_password_hash(agency_data['password_hash'], password):
            agency = Agency(agency_data['id'], agency_data['name'], agency_data['email'], agency_data['description'])
            login_user(agency)
            return redirect(url_for('agency_dashboard'))
        else:
            flash('Invalid email or password', 'error')

    return render_template('agency_login.html')

@app.route('/agency/logout')
@login_required
def agency_logout():
    logout_user()
    flash('logout succesfull!', 'success')
    return redirect(url_for('index'))

@app.route('/agency/dashboard')
@login_required
def agency_dashboard():
    """Agency dashboard showing all reports"""
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('''
        SELECT * FROM reports 
        WHERE agency_id = %s 
        ORDER BY created_at DESC
    ''', (current_user.id,))
    reports = cur.fetchall()
    cur.close()
    conn.close()
    
    return render_template('agency_dashboard.html', reports=reports)

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    """Serve uploaded images with proper content type"""
    from flask import send_from_directory
    # Validate filename to prevent directory traversal
    if '..' in filename or '/' in filename or '\\' in filename:
        return "Invalid filename", 400
    
    # Check file extension
    if not allowed_file(filename):
        return "Invalid file type", 400
    
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)
    
if __name__ == '__main__':
    # Only run the server if no special command line arguments are provided
    if len(sys.argv) > 1 and sys.argv[1] == 'init_db':
        print("Initializing database tables...")
        init_db()
        print("Database initialized. You can now run the server.")
    else:
        app.run(debug=True)