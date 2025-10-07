from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, make_response
from flask_migrate import Migrate
from flask_bcrypt import Bcrypt
from flask_login import LoginManager
from datetime import datetime, date, timedelta, UTC
import os
import logging

# Import db and all models from models.py
from models import (
    db, User, Company, Location, Guard, Attendance, DeletedAttendance,
    GuardComment, ShiftOverride, PayrollTracking, NotificationSettings,
    Notification, AttendanceDeadline, Request
)

# Import reports
from reports import ReportGenerator

# Set up logging
logging.basicConfig(level=logging.INFO)

# ============================================================================
# APP CONFIGURATION
# ============================================================================

app = Flask(__name__)

# Database configuration
basedir = os.path.abspath(os.path.dirname(__file__))

# --- DATABASE CONFIGURATION (Production Ready) ---
DATABASE_URL = os.environ.get('DATABASE_URL')

if DATABASE_URL is None:
    # Use local SQLite for genuine local testing
    SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(basedir, 'app.db')
else:
    # Use PostgreSQL on deployment. Replaces 'postgres://' with 'postgresql://' for SQLAlchemy compatibility.
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    SQLALCHEMY_DATABASE_URI = DATABASE_URL

# Flask config
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
app.config['SQLALCHEMY_DATABASE_URI'] = SQLALCHEMY_DATABASE_URI
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize extensions
db.init_app(app)
migrate = Migrate(app, db)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'

# ============================================================================
# CONSTANTS
# ============================================================================

ATTENDANCE_WRITE_ROLES = ['Supervisor', 'Business Support Officer']

REPORTING_ROLES = [
    'Ops Manager', 'HR Officer', 'Finance', 'General Manager',
    'Business Support Officer', 'Administrator'
]

DEFAULT_USERS = [
    ("admin", "admin2025", "Administrator"),
    ("supervisor", "sup2025", "Supervisor"),
    ("ops", "ops2025", "Ops Manager"),
    ("hr", "hr2025", "HR Officer"),
    ("finance", "fin2025", "Finance"),
    ("bso", "bso2025", "Business Support Officer"),
    ("gm", "gm2025", "General Manager")
]

# ============================================================================
# FLASK-LOGIN USER LOADER
# ============================================================================

@login_manager.user_loader
def load_user(user_id):
    """Callback for loading a user from the database given their ID."""
    return User.query.get(int(user_id))

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def check_write_access():
    """Check if user has write access for attendance operations"""
    if 'username' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    if session.get('role') not in ATTENDANCE_WRITE_ROLES:
        return jsonify({'error': 'Access denied - insufficient permissions'}), 403
    
    return None  # None means access is granted


# ============================================================================
# NOTIFICATION SERVICE FUNCTIONS
# ============================================================================

def create_notification(recipient_username, recipient_role, title, message, 
                       notification_type='info', category='system', 
                       reference_id=None, reference_type=None, 
                       scheduled_for=None, expires_in_hours=24):
    """Create a new notification"""
    expires_at = None
    if expires_in_hours:
        expires_at = datetime.now(UTC) + timedelta(hours=expires_in_hours)
    
    notification = Notification(
        recipient_username=recipient_username,
        recipient_role=recipient_role,
        title=title,
        message=message,
        notification_type=notification_type,
        category=category,
        reference_id=reference_id,
        reference_type=reference_type,
        scheduled_for=scheduled_for or datetime.now(UTC),
        expires_at=expires_at
    )
    
    db.session.add(notification)
    db.session.commit()
    return notification

def notify_attendance_reminder(shift_type='day'):
    """Send attendance reminders to supervisors"""
    supervisors = User.query.filter_by(role='Supervisor').all()
    
    for supervisor in supervisors:
        settings = get_notification_settings(supervisor.username)
        
        if not settings.in_app_notifications:
            continue
            
        current_time = datetime.now().strftime('%H:%M')
        reminder_time = settings.day_shift_reminder_time if shift_type == 'day' else settings.night_shift_reminder_time
        
        # Check if it's time to send reminder
        if current_time >= reminder_time:
            emoji = '☀️' if shift_type == 'day' else '🌙'
            title = f"{emoji} {shift_type.capitalize()} Shift Attendance Due"
            message = f"Good {'morning' if shift_type == 'day' else 'evening'}! Please submit {shift_type} shift attendance for all your locations."
            
            create_notification(
                recipient_username=supervisor.username,
                recipient_role=supervisor.role,
                title=title,
                message=message,
                notification_type='reminder',
                category='attendance',
                expires_in_hours=4
            )

def notify_attendance_overdue(minutes_overdue=30):
    """Send overdue attendance notifications"""
    supervisors = User.query.filter_by(role='Supervisor').all()
    
    for supervisor in supervisors:
        settings = get_notification_settings(supervisor.username)
        
        # Check for overdue attendance
        today = date.today()
        current_time = datetime.now()
        
        # Check day shift (should be submitted by 10:30 AM if due at 9:00 AM)
        day_deadline = datetime.combine(today, datetime.strptime(settings.day_shift_reminder_time, '%H:%M').time())
        day_overdue_time = day_deadline + timedelta(minutes=minutes_overdue)
        
        # Check night shift (should be submitted by 20:30 PM if due at 7:00 PM)  
        night_deadline = datetime.combine(today, datetime.strptime(settings.night_shift_reminder_time, '%H:%M').time())
        night_overdue_time = night_deadline + timedelta(minutes=minutes_overdue)
        
        if current_time >= day_overdue_time:
            # Check if day attendance was submitted
            day_attendance_count = Attendance.query.filter_by(date=today, shift='day', marked_by=supervisor.username).count()
            if day_attendance_count == 0:
                urgency = 'urgent' if minutes_overdue >= 120 else 'alert'
                title = f"{'🚨 URGENT' if urgency == 'urgent' else '⚠️'}: Day Attendance Overdue"
                message = f"Day shift attendance is {minutes_overdue} minutes overdue. Please submit immediately to avoid delays."
                
                create_notification(
                    recipient_username=supervisor.username,
                    recipient_role=supervisor.role,
                    title=title,
                    message=message,
                    notification_type=urgency,
                    category='attendance',
                    expires_in_hours=2
                )
        
        if current_time >= night_overdue_time:
            # Check if night attendance was submitted
            night_attendance_count = Attendance.query.filter_by(date=today, shift='night', marked_by=supervisor.username).count()
            if night_attendance_count == 0:
                urgency = 'urgent' if minutes_overdue >= 120 else 'alert'
                title = f"{'🚨 URGENT' if urgency == 'urgent' else '⚠️'}: Night Attendance Overdue"
                message = f"Night shift attendance is {minutes_overdue} minutes overdue. Please submit immediately to avoid delays."
                
                create_notification(
                    recipient_username=supervisor.username,
                    recipient_role=supervisor.role,
                    title=title,
                    message=message,
                    notification_type=urgency,
                    category='attendance',
                    expires_in_hours=2
                )

def notify_office_staff_attendance_submitted(supervisor_username, shift, location_count, guard_count):
    """Notify office staff when supervisor submits attendance"""
    office_roles = ['Ops Manager', 'HR Officer', 'General Manager']
    office_staff = User.query.filter(User.role.in_(office_roles)).all()
    
    for staff in office_staff:
        settings = get_notification_settings(staff.username)
        
        if settings.notify_attendance_submitted:
            emoji = '☀️' if shift == 'day' else '🌙'
            title = f"{emoji} Attendance Submitted"
            message = f"Supervisor {supervisor_username} submitted {shift} shift attendance for {location_count} locations ({guard_count} guards total)."
            
            create_notification(
                recipient_username=staff.username,
                recipient_role=staff.role,
                title=title,
                message=message,
                notification_type='info',
                category='attendance',
                reference_type='attendance_summary',
                expires_in_hours=48
            )

def notify_new_request_submitted(request_obj):
    """Notify relevant office staff when new request is submitted"""
    # Determine who should be notified based on request type
    role_mapping = {
        'HR': ['HR Officer', 'General Manager'],
        'Finance': ['Finance', 'General Manager'],
        'Ops': ['Ops Manager', 'General Manager'],
        'Inventory': ['Ops Manager', 'General Manager'],
        'Incident': ['Ops Manager', 'HR Officer', 'General Manager'],
        'Leave': ['HR Officer', 'General Manager'],
        'Permission': ['Ops Manager', 'HR Officer']
    }
    
    relevant_roles = role_mapping.get(request_obj.type, ['General Manager'])
    office_staff = User.query.filter(User.role.in_(relevant_roles)).all()
    
    for staff in office_staff:
        settings = get_notification_settings(staff.username)
        
        if settings.notify_new_requests:
            title = f"📋 New {request_obj.type} Request"
            message = f"{request_obj.from_user} submitted a {request_obj.type} request: '{request_obj.description[:100]}{'...' if len(request_obj.description) > 100 else ''}'"
            
            create_notification(
                recipient_username=staff.username,
                recipient_role=staff.role,
                title=title,
                message=message,
                notification_type='info',
                category='request',
                reference_id=request_obj.id,
                reference_type='request',
                expires_in_hours=72
            )

def notify_guard_issue_pattern(guard_id, issue_type, pattern_description):
    """Notify about guard attendance patterns or issues"""
    office_roles = ['Ops Manager', 'HR Officer']
    office_staff = User.query.filter(User.role.in_(office_roles)).all()
    
    guard = Guard.query.get(guard_id)
    if not guard:
        return
    
    for staff in office_staff:
        settings = get_notification_settings(staff.username)
        
        if settings.notify_guard_issues:
            title = f"⚠️ Guard Pattern Alert"
            message = f"{guard.name} at {guard.location.name}: {pattern_description}"
            
            create_notification(
                recipient_username=staff.username,
                recipient_role=staff.role,
                title=title,
                message=message,
                notification_type='alert',
                category='guard_issue',
                reference_id=guard_id,
                reference_type='guard',
                expires_in_hours=48
            )

def get_notification_settings(username):
    """Get or create notification settings for a user"""
    settings = NotificationSettings.query.filter_by(username=username).first()
    
    if not settings:
        user = User.query.filter_by(username=username).first()
        if user:
            settings = NotificationSettings(
                username=username,
                role=user.role
            )
            db.session.add(settings)
            db.session.commit()
    
    return settings

def cleanup_old_notifications():
    """Clean up expired notifications"""
    expired = Notification.query.filter(
        Notification.expires_at < datetime.utcnow()
    ).all()
    
    for notification in expired:
        db.session.delete(notification)
    
    db.session.commit()
    return len(expired)

# ============================================================================
# INITIALIZATION FUNCTIONS
# ============================================================================

def init_database():
    """Initialize database with sample data"""
    with app.app_context():
        db.create_all()
        print("✅ Database tables created successfully.")
        
        # Check if data already exists
        if User.query.first():
            print("ℹ️ Database already initialized.")
            return
        
        print("📝 Seeding initial data...")
        
        # Create users with bcrypt hashed passwords
        for username, password, role in DEFAULT_USERS:
            user = User(username=username, role=role)
            user.set_password(password, bcrypt)  # Pass bcrypt instance
            db.session.add(user)

                
        # Create companies
        companies = [
            Company(name='TAYSEC'),
            Company(name='G29'),
            Company(name='BROLL'),
            Company(name='MINOR')
        ]
        db.session.add_all(companies)
        db.session.commit()
        
        # Get company IDs
        taysec = Company.query.filter_by(name='TAYSEC').first()
        g29 = Company.query.filter_by(name='G29').first()
        broll = Company.query.filter_by(name='BROLL').first()
        minor = Company.query.filter_by(name='MINOR').first()
        
        # Create locations 
        locations_data = [
            # TAYSEC Locations
            {'name': 'Alema Court', 'company_id': taysec.id, 'is_accessible': False},
            {'name': 'Cedar Court', 'company_id': taysec.id, 'is_accessible': True},
            {'name': 'Enterprise Gardens', 'company_id': taysec.id, 'is_accessible': True},
            {'name': 'Hansen Court', 'company_id': taysec.id, 'is_accessible': True},
            {'name': 'Cantonment Gardens', 'company_id': taysec.id, 'is_accessible': True},
            {'name': 'Boadu Gardens', 'company_id': taysec.id, 'is_accessible': True},
            
            # G29 Locations
            {'name': 'Palm Court', 'company_id': g29.id, 'is_accessible': True},
            {'name': 'Acacia Court', 'company_id': g29.id, 'is_accessible': True},
            {'name': 'Bay Tree', 'company_id': g29.id, 'is_accessible': True},
            {'name': '9th Avenue', 'company_id': g29.id, 'is_accessible': True},
            
            # BROLL Locations
            {'name': 'Polo Court', 'company_id': broll.id, 'is_accessible': True},
            
            # MINOR Locations (including ACCRA MINOR with restricted access)
            {'name': 'Barbex', 'company_id': minor.id, 'is_accessible': True},
            {'name': 'BDZ Properties', 'company_id': minor.id, 'is_accessible': True},
            {'name': 'Admiral Homes', 'company_id': minor.id, 'is_accessible': True},
            {'name': 'Powa 1', 'company_id': minor.id, 'is_accessible': True},
            {'name': 'Otinibi Powa', 'company_id': minor.id, 'is_accessible': True},
            {'name': 'Little Campus', 'company_id': minor.id, 'is_accessible': True},
            {'name': 'Capella', 'company_id': minor.id, 'is_accessible': True},
            {'name': 'Daniella', 'company_id': minor.id, 'is_accessible': True},
            {'name': 'Qatar Charity', 'company_id': minor.id, 'is_accessible': True},
            {'name': 'Judge Amma', 'company_id': minor.id, 'is_accessible': True},
            {'name': 'Judge Amma 2', 'company_id': minor.id, 'is_accessible': True},
            {'name': 'KAMCCU', 'company_id': minor.id, 'is_accessible': True},
            {'name': 'PALB', 'company_id': minor.id, 'is_accessible': True},
            {'name': 'VN Commodities', 'company_id': minor.id, 'is_accessible': True},
            {'name': 'OM Kasoa', 'company_id': minor.id, 'is_accessible': True},
            
            # ACCRA MINOR (restricted access)
            {'name': 'Accra Tenesse', 'company_id': minor.id, 'is_accessible': True},
            {'name': 'Major Senyo', 'company_id': minor.id, 'is_accessible': True},
            {'name': 'ICGC', 'company_id': minor.id, 'is_accessible': True},
        ]
        
        for loc_data in locations_data:
            location = Location(**loc_data)
            db.session.add(location)
        
        db.session.commit()
        
        # Create guards (using your provided data)
        create_sample_guards()

        print("✅ Initial data seeded successfully!")

def create_sample_guards():
    """Create all guard data"""
    
    # Get location mapping
    locations = Location.query.all()
    location_map = {loc.name: loc.id for loc in locations}
    
    # Day shift guards data
    day_guards = [
        # TAYSEC Day Guards
        ('Emmanuel Offei', 'Alema Court'),
        ('Richard Abeiku', 'Alema Court'),
        ('Asafo-Adjei Antwi', 'Cedar Court'),
        ('Gabriel Kuukye', 'Cedar Court'),
        ('Emmanuel Kotei', 'Enterprise Gardens'),
        ('John Koomson', 'Enterprise Gardens'),
        ('Benjamin Asare', 'Hansen Court'),
        ('Darko Offei', 'Hansen Court'),
        ('Collins Amoako', 'Cantonment Gardens'),
        ('Evans Ampem', 'Cantonment Gardens'),
        ('Gifty Gogoe', 'Boadu Gardens'),
        ('Felix Tetteh (Supervisor)', 'Boadu Gardens'),
        
        # G29 Day Guards
        ('John Sabbah', 'Palm Court'),
        ('Kelvin Twumasi', 'Palm Court'),
        ('Paul Wilson', 'Acacia Court'),
        ('Richard Andoh', 'Acacia Court'),
        ('Emmanuel Quansah', 'Bay Tree'),
        ('Emmanuel Amoako', 'Bay Tree'),
        ('Yusif Cobbinah', '9th Avenue'),
        ('Daniel Gekye', '9th Avenue'),
        
        # BROLL Day Guards
        ('Enoch Dorgbetor', 'Polo Court'),
        ('George Ndollah', 'Polo Court'),
        
        # MINOR Day Guards
        ('Moses Adjei Mensah', 'Barbex'),
        ('Joshua Patu', 'BDZ Properties'),
        ('Asford Nyarko (Driver)', 'Admiral Homes'),
        ('Benneth Doe (Supervisor)', 'Powa 1'),
        ('Michael Ofosu Dankwa', 'Otinibi Powa'),
        ('Isaac Otoo', 'Little Campus'),
        ('Prince Adusei Danso', 'Little Campus'),
        ('Emmanuel Bentum', 'Capella'),
        ('Clement Adjei', 'Daniella'),
        ('Kofi Badu', 'Qatar Charity'),
        ('George Acquah', 'Judge Amma'),
        ('Joseph Anum Blebo', 'Judge Amma 2'),
        ('Alex Ohen Ofori', 'KAMCCU'),
        ('Michael Mac Dowuona', 'KAMCCU'),
        ('Gideon Yibor', 'PALB'),
        ('Nyarko Abronuma', 'VN Commodities'),
        ('Kwabena Boateng', 'OM Kasoa'),
        ('Monica Ofori', 'OM Kasoa'),
    ]
    
    # Night shift guards data
    night_guards = [
        # TAYSEC Night Guards
        ('John Kesse', 'Alema Court'),
        ('Clement Adjei', 'Alema Court'),
        ('Joseph Sawiri', 'Alema Court'),
        ('Evans Dadzie', 'Cedar Court'),
        ('Fennel Dery', 'Cedar Court'),
        ('Mohammed Zakari', 'Cedar Court'),
        ('Laura Kamburi', 'Enterprise Gardens'),
        ('Aziba Caezar', 'Enterprise Gardens'),
        ('Richard Dadzie', 'Enterprise Gardens'),
        ('Eric Addo', 'Hansen Court'),
        ('Kofi Addo', 'Hansen Court'),
        ('Quarshie Vieira', 'Hansen Court'),
        ('Bright Asamoah', 'Cantonment Gardens'),
        ('Emmanuel Adams', 'Cantonment Gardens'),
        ('Kasim Abubakar', 'Cantonment Gardens'),
        ('Alexander Bidoma', 'Boadu Gardens'),
        ('Bruce Assortey', 'Boadu Gardens'),
        
        # G29 Night Guards
        ('Moses Ahmed', 'Palm Court'),
        ('Sulley Yakubu', 'Palm Court'),
        ('Daniel Lincoln', 'Palm Court'),
        ('Abubakar Mohammed', 'Acacia Court'),
        ('Isaac Kyei', 'Acacia Court'),
        ('Isaac Awusi', 'Acacia Court'),
        ('Paul Ebo Dofu', 'Bay Tree'),
        ('Godwin Nelson', 'Bay Tree'),
        ('Quansah Emmanuel', 'Bay Tree'),
        ('Francis Hudinya', '9th Avenue'),
        ('George Amankra', '9th Avenue'),
        
        # ACCRA MINOR Night Guards (restricted access)
        ('Francis Akambacha', 'Accra Tenesse'),
        ('Lamptey Ishmael', 'Accra Tenesse'),
        ('Anthony Bekoe', 'Major Senyo'),
        ('Philip Adu-Boateng', 'ICGC'),
        
        # MINOR Night Guards
        ('Clement Kanjeib', 'Admiral Homes'),
        ('Prosper Nuquaye', 'Barbex'),
        ('Gaddiel Haizel', 'Barbex'),
        ('Ebenezer Tetteh', 'BDZ Properties'),
        ('Bismark Fiamawle', 'Capella'),
        ('Vitus Sagbe', 'Capella'),
        ('Paul Akanjak', 'Powa 1'),
        ('Joshua Ampofo', 'Powa 1'),
        ('Kwabena Lamptey', 'Little Campus'),
        ('Emmanuel Bentum Koomson', 'Little Campus'),
        ('Moses Sefah', 'Daniella'),
        ('Adams Alhassan', 'Judge Amma'),
        ('Gideon Norgbe', 'Judge Amma 2'),
        ('Robert Lartey', 'KAMCCU'),
        ('Oscar Tomani', 'KAMCCU'),
        ('James Afenyi', 'Qatar Charity'),
        ('Roland Dadzie', 'PALB'),
        ('Patrick Adofo', 'Otinibi Powa'),
        ('Courage Okyere', 'VN Commodities'),
        ('Samuel Gyedu', 'OM Kasoa'),
        ('Yaw Noamessi', 'OM Kasoa'),
    ]
    
    # Create day shift guards
    for guard_name, location_name in day_guards:
        if location_name in location_map:
            role = 'supervisor' if 'supervisor' in guard_name.lower() else ('driver' if 'driver' in guard_name.lower() else 'guard')
            guard = Guard(
                name=guard_name,
                location_id=location_map[location_name],
                shift_type='day',
                role=role
            )
            db.session.add(guard)
    
    # Create night shift guards
    for guard_name, location_name in night_guards:
        if location_name in location_map:
            role = 'supervisor' if 'supervisor' in guard_name.lower() else ('driver' if 'driver' in guard_name.lower() else 'guard')
            guard = Guard(
                name=guard_name,
                location_id=location_map[location_name],
                shift_type='night',
                role=role
            )
            db.session.add(guard)
    
    db.session.commit()

# ============================================================================
# AUTHENTICATION ROUTES
# ============================================================================

@app.route('/')
def index():
    if 'username' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password, bcrypt):
            session['username'] = user.username
            session['role'] = user.role
            flash('Login successful!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid credentials', 'error')
    
    return render_template('login.html', current_year=datetime.now().year)


@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out', 'success')
    return redirect(url_for('login'))

# ============================================================================
# DASHBOARD ROUTE
# ============================================================================

@app.route('/dashboard')
def dashboard():
    if 'username' not in session:
        return redirect(url_for('login'))
    
    role = session.get('role')
    return render_template('dashboard.html', role=role)

@app.route('/admin')
def admin_dashboard():
    """Admin-only dashboard for system management"""
    if 'username' not in session:
        return redirect(url_for('login'))
    
    # Only Administrator role can access
    if session.get('role') != 'Administrator':
        flash('Access denied - Administrator privileges required', 'error')
        return redirect(url_for('dashboard'))
    
    # Get system statistics
    total_guards = Guard.query.count()
    active_guards = Guard.query.filter_by(is_active=True).count() if hasattr(Guard, 'is_active') else total_guards
    total_locations = Location.query.count()
    active_locations = Location.query.filter_by(is_accessible=True).count()
    total_users = User.query.count()
    
    stats = {
        'total_guards': total_guards,
        'active_guards': active_guards,
        'total_locations': total_locations,
        'active_locations': active_locations,
        'total_users': total_users
    }
    
    return render_template('admin_dashboard.html', stats=stats)

# ============================================================================
# REQUEST MANAGEMENT ROUTES
# ============================================================================

@app.route('/new_request', methods=['GET', 'POST'])
def new_request():
    if 'username' not in session:
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        req_type = request.form['type']
        description = request.form['description']
        
        new_req = Request(
            from_user=session['username'],
            role=session['role'],
            type=req_type,
            description=description
        )
        db.session.add(new_req)
        db.session.commit()
        
        flash('Request submitted successfully!', 'success')
        return redirect(url_for('dashboard'))
    
    return render_template('new_request.html')

@app.route('/view_requests')
def view_requests():
    if 'username' not in session:
        return redirect(url_for('login'))
    
    role = session.get('role')
    current_user = session.get('username')
    
    # Filter requests based on role
    if role == 'Supervisor':
        # The column name is likely 'from_user' or 'submitted_by'
        # Let's assume it's 'from_user' based on your code
        requests = Request.query.filter_by(from_user=current_user).order_by(Request.submitted_at.desc()).all()
    else:
        requests = Request.query.order_by(Request.submitted_at.desc()).all()
    
    # Format dates for display
    for req in requests:
        req.submitted_at = req.submitted_at.strftime('%Y-%m-%d %H:%M:%S')
        if req.responded_at:
            req.responded_at = req.responded_at.strftime('%Y-%m-%d %H:%M:%S')
    
    return render_template('view_requests.html', requests=requests, role=role, current_user=current_user)
    
@app.route('/update_request/<int:req_id>', methods=['POST'])
def update_request(req_id):
    if 'username' not in session:
        return redirect(url_for('login'))
    
    role = session.get('role')
    
    # Only certain roles can update requests
    if role not in ["Ops Manager", "HR Officer", "Finance", "Training Officer", "Business Support Officer"]:
        flash('Access denied', 'error')
        return redirect(url_for('view_requests'))
    
    req = Request.query.get_or_404(req_id)
    new_status = request.form['status']
    
    req.status = new_status
    req.updated_by = session['username']
    if new_status != 'Pending':
        req.responded_at = datetime.utcnow()
    
    db.session.commit()
    flash('Request updated successfully!', 'success')
    return redirect(url_for('view_requests'))

def check_write_access():
    """Check if user has write access for attendance operations"""
    if 'username' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    if session.get('role') not in ATTENDANCE_WRITE_ROLES:
        return jsonify({'error': 'Access denied - insufficient permissions'}), 403
    
    return None  # None means access is granted

# ============================================================================
# ADMIN API ROUTES (Add these to your app.py after line ~850)
# ============================================================================

@app.route('/api/admin/guards')
def admin_get_guards():
    """Get all guards for admin management"""
    if 'username' not in session or session.get('role') != 'Administrator':
        return jsonify({'error': 'Access denied'}), 403
    
    guards = Guard.query.join(Location).join(Company).all()
    result = []
    
    for guard in guards:
        result.append({
            'id': guard.id,
            'name': guard.name,
            'location_id': guard.location_id,
            'location_name': guard.location.name,
            'company_name': guard.location.company.name,
            'shift_type': guard.shift_type,
            'role': guard.role,
            'is_active': guard.is_active if hasattr(guard, 'is_active') else True,
            'resigned_date': guard.resigned_date.strftime('%Y-%m-%d') if hasattr(guard, 'resigned_date') and guard.resigned_date else None,
            'notes': guard.notes if hasattr(guard, 'notes') else ''
        })
    
    return jsonify(result)

@app.route('/api/admin/guards', methods=['POST'])
def admin_add_guard():
    """Add a new guard"""
    if 'username' not in session or session.get('role') != 'Administrator':
        return jsonify({'error': 'Access denied'}), 403
    
    data = request.get_json()
    
    # Validate required fields
    if not all(k in data for k in ['name', 'location_id', 'shift_type']):
        return jsonify({'error': 'Missing required fields'}), 400
    
    try:
        new_guard = Guard(
            name=data['name'],
            location_id=data['location_id'],
            shift_type=data['shift_type'],
            role=data.get('role', 'guard'),
            is_active=True
        )
        
        db.session.add(new_guard)
        db.session.commit()
        
        return jsonify({
            'success': True, 
            'message': f"Guard {data['name']} added successfully",
            'guard_id': new_guard.id
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Failed to add guard: {str(e)}'}), 500

@app.route('/api/admin/guards/<int:guard_id>', methods=['PUT'])
def admin_update_guard(guard_id):
    """Update guard details"""
    if 'username' not in session or session.get('role') != 'Administrator':
        return jsonify({'error': 'Access denied'}), 403
    
    guard = Guard.query.get_or_404(guard_id)
    data = request.get_json()
    
    try:
        if 'name' in data:
            guard.name = data['name']
        if 'location_id' in data:
            guard.location_id = data['location_id']
        if 'shift_type' in data:
            guard.shift_type = data['shift_type']
        if 'role' in data:
            guard.role = data['role']
        if 'notes' in data and hasattr(guard, 'notes'):
            guard.notes = data['notes']
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f"Guard {guard.name} updated successfully"
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Failed to update guard: {str(e)}'}), 500

@app.route('/api/admin/guards/<int:guard_id>/deactivate', methods=['POST'])
def admin_deactivate_guard(guard_id):
    """Deactivate a guard (soft delete)"""
    if 'username' not in session or session.get('role') != 'Administrator':
        return jsonify({'error': 'Access denied'}), 403
    
    guard = Guard.query.get_or_404(guard_id)
    data = request.get_json()
    
    try:
        if hasattr(guard, 'is_active'):
            guard.is_active = False
        if hasattr(guard, 'resigned_date'):
            guard.resigned_date = datetime.strptime(data.get('resigned_date', date.today().isoformat()), '%Y-%m-%d').date()
        if hasattr(guard, 'notes') and data.get('reason'):
            guard.notes = f"Deactivated: {data['reason']}"
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f"Guard {guard.name} deactivated successfully"
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Failed to deactivate guard: {str(e)}'}), 500

@app.route('/api/admin/guards/<int:guard_id>/reactivate', methods=['POST'])
def admin_reactivate_guard(guard_id):
    """Reactivate a deactivated guard"""
    if 'username' not in session or session.get('role') != 'Administrator':
        return jsonify({'error': 'Access denied'}), 403
    
    guard = Guard.query.get_or_404(guard_id)
    
    try:
        if hasattr(guard, 'is_active'):
            guard.is_active = True
        if hasattr(guard, 'resigned_date'):
            guard.resigned_date = None
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f"Guard {guard.name} reactivated successfully"
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Failed to reactivate guard: {str(e)}'}), 500

# ============================================================================
# LOCATION MANAGEMENT API ROUTES
# ============================================================================

@app.route('/api/admin/locations')
def admin_get_locations():
    """Get all locations for admin management"""
    if 'username' not in session or session.get('role') != 'Administrator':
        return jsonify({'error': 'Access denied'}), 403
    
    locations = Location.query.join(Company).all()
    result = []
    
    for location in locations:
        guard_count = Guard.query.filter_by(location_id=location.id).count()
        active_guard_count = Guard.query.filter_by(location_id=location.id, is_active=True).count() if hasattr(Guard, 'is_active') else guard_count
        
        result.append({
            'id': location.id,
            'name': location.name,
            'company_id': location.company_id,
            'company_name': location.company.name,
            'is_accessible': location.is_accessible,
            'guard_count': guard_count,
            'active_guard_count': active_guard_count
        })
    
    return jsonify(result)

@app.route('/api/admin/locations', methods=['POST'])
def admin_add_location():
    """Add a new location"""
    if 'username' not in session or session.get('role') != 'Administrator':
        return jsonify({'error': 'Access denied'}), 403
    
    data = request.get_json()
    
    if not all(k in data for k in ['name', 'company_id']):
        return jsonify({'error': 'Missing required fields'}), 400
    
    try:
        new_location = Location(
            name=data['name'],
            company_id=data['company_id'],
            is_accessible=data.get('is_accessible', True)
        )
        
        db.session.add(new_location)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f"Location {data['name']} added successfully",
            'location_id': new_location.id
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Failed to add location: {str(e)}'}), 500

@app.route('/api/admin/locations/<int:location_id>', methods=['PUT'])
def admin_update_location(location_id):
    """Update location details"""
    if 'username' not in session or session.get('role') != 'Administrator':
        return jsonify({'error': 'Access denied'}), 403
    
    location = Location.query.get_or_404(location_id)
    data = request.get_json()
    
    try:
        if 'name' in data:
            location.name = data['name']
        if 'company_id' in data:
            location.company_id = data['company_id']
        if 'is_accessible' in data:
            location.is_accessible = data['is_accessible']
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f"Location {location.name} updated successfully"
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Failed to update location: {str(e)}'}), 500

@app.route('/api/admin/locations/<int:location_id>/toggle', methods=['POST'])
def admin_toggle_location(location_id):
    """Toggle location accessibility (soft delete/reactivate)"""
    if 'username' not in session or session.get('role') != 'Administrator':
        return jsonify({'error': 'Access denied'}), 403
    
    location = Location.query.get_or_404(location_id)
    
    try:
        location.is_accessible = not location.is_accessible
        db.session.commit()
        
        status = "activated" if location.is_accessible else "deactivated"
        return jsonify({
            'success': True,
            'message': f"Location {location.name} {status} successfully",
            'is_accessible': location.is_accessible
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Failed to toggle location: {str(e)}'}), 500

# ============================================================================
# COMPANY MANAGEMENT API ROUTES
# ============================================================================

@app.route('/api/admin/companies')
def admin_get_companies():
    """Get all companies"""
    if 'username' not in session or session.get('role') != 'Administrator':
        return jsonify({'error': 'Access denied'}), 403
    
    companies = Company.query.all()
    result = []
    
    for company in companies:
        location_count = Location.query.filter_by(company_id=company.id).count()
        active_location_count = Location.query.filter_by(company_id=company.id, is_accessible=True).count()
        
        result.append({
            'id': company.id,
            'name': company.name,
            'location_count': location_count,
            'active_location_count': active_location_count
        })
    
    return jsonify(result)

# ============================================================================
# ATTENDANCE ROUTES
# ============================================================================

@app.route('/attendance')
def attendance():
    """Renders the main attendance marking screen."""
    if 'username' not in session:
        return redirect(url_for('login'))
    
    # Use the centralized constant to allow Supervisor and BSO access
    if session.get('role') not in ATTENDANCE_WRITE_ROLES:
        flash('Access denied - insufficient permissions to view the marking dashboard.', 'error')
        return redirect(url_for('dashboard'))
    
    # If authorized, render the attendance template
    return render_template('attendance.html')

# Enhanced view_attendance route with filtering
# REPLACE your existing @app.route('/view_attendance') function with this:

@app.route('/view_attendance')
def view_attendance():
    if 'username' not in session:
        return redirect(url_for('login'))
    
    # Get filter parameters
    company_filter = request.args.get('company', '')
    location_filter = request.args.get('location', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    shift_filter = request.args.get('shift', '')
    status_filter = request.args.get('status', '')
    
    # Base query
    query = db.session.query(
        Attendance, Guard, Location, Company
    ).join(
        Guard, Attendance.guard_id == Guard.id
    ).join(
        Location, Guard.location_id == Location.id
    ).join(
        Company, Location.company_id == Company.id
    )
    
    # Apply filters
    if company_filter:
        query = query.filter(Company.name == company_filter)
    
    if location_filter:
        query = query.filter(Location.name.ilike(f'%{location_filter}%'))
    
    if date_from:
        query = query.filter(Attendance.date >= datetime.strptime(date_from, '%Y-%m-%d').date())
    
    if date_to:
        query = query.filter(Attendance.date <= datetime.strptime(date_to, '%Y-%m-%d').date())
    
    if shift_filter:
        query = query.filter(Attendance.shift == shift_filter)
    
    if status_filter:
        query = query.filter(Attendance.status == status_filter)
    
    # Execute query with ordering
    attendance_records = query.order_by(
        Attendance.date.desc(), 
        Attendance.timestamp.desc()
    ).all()
    
    # Get latest comments for each guard
    for attendance, guard, location, company in attendance_records:
        latest_comment = GuardComment.query.filter_by(
            guard_id=guard.id,
            is_active=True
        ).order_by(GuardComment.created_at.desc()).first()
        
        if latest_comment:
            attendance.notes = latest_comment.comment
    
    # Get all companies for filter dropdown
    companies = Company.query.all()
    
    return render_template(
        'view_attendance.html', 
        attendance_records=attendance_records,
        companies=companies,
        filters={
            'company': company_filter,
            'location': location_filter,
            'date_from': date_from,
            'date_to': date_to,
            'shift': shift_filter,
            'status': status_filter
        }
    )
# ============================================================================
# API ROUTES FOR ATTENDANCE
# ============================================================================

@app.route('/api/all-locations')
def get_all_locations():
    """Get all locations including non-accessible ones for display"""
    locations = Location.query.all()
    result = []
    for location in locations:
        result.append({
            'id': location.id,
            'name': location.name,
            'company': location.company.name,
            'is_accessible': location.is_accessible
        })
    return jsonify(result)

@app.route('/api/locations')
def get_locations():
    """Get only accessible locations"""
    locations = Location.query.filter_by(is_accessible=True).all()
    result = []
    for location in locations:
        result.append({
            'id': location.id,
            'name': location.name,
            'company': location.company.name,
            'is_accessible': location.is_accessible
        })
    return jsonify(result)

@app.route('/api/guards/<int:location_id>/<shift>')
def get_guards(location_id, shift):
    """
    Get guards for a specific location and shift, including overrides. 
    Now includes authorization check for Supervisor/BSO access.
    (Function name reverted to 'get_guards' as requested.)
    """
    # --- Authorization Check (REQUIRED: Supervisor/BSO only) ---
    if 'username' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    if session.get('role') not in ATTENDANCE_WRITE_ROLES:
        # Access is denied if the user is not a Supervisor or BSO
        return jsonify({'error': 'Access denied to fetch guard data for marking.'}), 403
    
    # --- Start of the detailed guard retrieval logic ---
    location = Location.query.get_or_404(location_id)
    if not location.is_accessible:
        return jsonify({'error': 'Access denied: Location is not accessible'}), 403
    
    # Get regular guards assigned to this location and shift
    regular_guards = Guard.query.filter_by(location_id=location_id, shift_type=shift).all()
    
    # Get guards temporarily assigned to this location for today
    today = date.today()
    temp_overrides = ShiftOverride.query.filter_by(
        override_location_id=location_id,
        override_shift=shift,
        date=today,
        is_active=True
    ).all()
    
    # Initialize the final list
    result = []
    
    # Process regular guards
    for guard in regular_guards:
        # Check if this guard has an override for today
        override = ShiftOverride.query.filter_by(
            guard_id=guard.id,
            date=today,
            is_active=True
        ).first()
        
        # Skip if guard is reassigned to different location (they won't be working here)
        if override and override.override_location_id != location_id:
            continue
            
        # Get attendance
        attendance = Attendance.query.filter_by(
            guard_id=guard.id,
            date=today,
            shift=shift
        ).first()
        
        guard_data = {
            'id': guard.id,
            'name': guard.name,
            'role': guard.role,
            'status': attendance.status if attendance else None,
            'notes': attendance.notes if attendance else '',
            'default_shift': guard.shift_type,
            'current_shift': override.override_shift if override else guard.shift_type,
            'has_override': override is not None,
            'is_temporary': False  # This is their regular location
        }
        
        if override:
            guard_data.update({
                'override_reason': override.reason,
                'is_shift_changed': override.original_shift != override.override_shift,
                'is_location_changed': override.original_location_id != override.override_location_id
            })
        
        result.append(guard_data)
    
    # Process temporarily assigned guards (those whose override points to this location)
    for override in temp_overrides:
        guard = override.guard
        
        # Skip if we already included this guard (prevents duplicates if a regular guard
        # has a shift change override but stayed at the same location/shift)
        if any(g['id'] == guard.id for g in result):
            continue
            
        # Get attendance
        attendance = Attendance.query.filter_by(
            guard_id=guard.id,
            date=today,
            shift=shift
        ).first()
        
        guard_data = {
            'id': guard.id,
            'name': guard.name,
            'role': guard.role,
            'status': attendance.status if attendance else None,
            'notes': attendance.notes if attendance else '',
            'default_shift': guard.shift_type,
            'current_shift': override.override_shift,
            'has_override': True,
            'is_temporary': True,  # This guard is temporarily here
            'override_reason': override.reason,
            # Assuming original_location and original_location.company are accessible via override.
            'original_location': override.original_location.name, 
            'original_company': override.original_location.company.name,
            'is_shift_changed': override.original_shift != override.override_shift,
            'is_location_changed': True
        }
        
        result.append(guard_data)
    
    return jsonify(result)

@app.route('/api/mark-attendance', methods=['POST'])
def mark_attendance():
    """Mark attendance for a specific guard (CREATE or UPDATE)"""
    
    # Use helper to check authentication and authorization (401/403 errors handled here)
    access_check = check_write_access()
    if access_check:
        return access_check
    
    try:
        data = request.get_json()
        guard_id = data['guard_id']
        status = data['status']
        shift = data['shift']
        notes = data.get('notes', '')
        
        # Guard verification and location accessibility check (keep this)
        guard = Guard.query.get_or_404(guard_id)
        if not guard.location.is_accessible:
            return jsonify({'error': 'Guard assigned to an inaccessible location'}), 403

        # 1. Find existing attendance record for today and shift
        attendance = Attendance.query.filter_by(
            guard_id=guard_id,
            date=date.today(),
            shift=shift
        ).first()

        # -----------------------------------------------------------------
        # FIX APPLIED: Removed the strict "if attendance and attendance.status"
        # check that was causing 409 errors on valid updates.
        # -----------------------------------------------------------------
        
        if attendance:
            # UPDATE existing record
            attendance.status = status
            attendance.notes = notes
            attendance.marked_by = session['role']
            attendance.timestamp = datetime.utcnow()
            message = f"Attendance updated for {guard.name}."
        else:
            # CREATE new record
            attendance = Attendance(
                guard_id=guard_id,
                status=status,
                shift=shift,
                notes=notes,
                marked_by=session['role']
            )
            db.session.add(attendance)
            message = f"Attendance recorded for {guard.name}."
        
        db.session.commit()
        return jsonify({'success': True, 'message': message})

    except KeyError:
        return jsonify({'error': 'Missing required data fields in request.'}), 400
    except Exception as e:
        db.session.rollback()
        # Fallback for unexpected errors (e.g., database connection issues)
        print(f"Server Error during attendance marking: {e}")
        return jsonify({'error': 'An internal server error occurred during processing.'}), 500

@app.route('/api/bulk-mark', methods=['POST'])
def bulk_mark_attendance():
    """Bulk mark attendance for all guards at a location"""
    # 1. Enforce Role Check (Only Supervisor/BSO)
    auth_check = check_write_access()
    if auth_check:
        return auth_check
    
    data = request.get_json()
    location_id = data['location_id']
    shift = data['shift']
    status = data['status']
    
    # Verify location is accessible
    location = Location.query.get_or_404(location_id)
    if not location.is_accessible:
        return jsonify({'error': 'Access denied'}), 403
    
    guards = Guard.query.filter_by(location_id=location_id, shift_type=shift).all()
    
    marked_count = 0
    skipped_count = 0

    for guard in guards:
        attendance = Attendance.query.filter_by(
            guard_id=guard.id,
            date=date.today(),
            shift=shift
        ).first()
        
        # 2. Deactivation Logic for Bulk: Skip if already marked
        if attendance and attendance.status:
            skipped_count += 1
            continue
        
        if attendance:
            attendance.status = status
            attendance.marked_by = session['role']
            attendance.timestamp = datetime.utcnow()
        else:
            attendance = Attendance(
                guard_id=guard.id,
                status=status,
                shift=shift,
                marked_by=session['role']
            )
            db.session.add(attendance)
        
        marked_count += 1
    
    db.session.commit()
    
    message = f'{marked_count} guards marked successfully.'
    if skipped_count > 0:
         message += f' ({skipped_count} skipped as they were already marked.)'

    return jsonify({'success': True, 'toast_message': message})

@app.route('/api/guard-comments/<int:guard_id>')
def get_guard_comments(guard_id):
    """Get all comments for a specific guard (Requires any authenticated user to view)"""
    if 'username' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    comments = GuardComment.query.filter_by(guard_id=guard_id, is_active=True)\
                                 .order_by(GuardComment.created_at.desc()).all()
    
    result = []
    for comment in comments:
        result.append({
            'id': comment.id,
            'comment': comment.comment,
            'type': comment.comment_type,
            'created_by': comment.created_by,
            'created_at': comment.created_at.strftime('%Y-%m-%d %H:%M'),
            'guard_name': comment.guard.name
        })
    
    return jsonify(result)

@app.route('/api/add-guard-comment', methods=['POST'])
def add_guard_comment():
    """Add a new comment for a guard (Requires any authenticated user to create)"""
    if 'username' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    data = request.get_json()
    guard_id = data['guard_id']
    comment = data['comment']
    comment_type = data.get('type', 'note')
    
    guard = Guard.query.get_or_404(guard_id)
    
    new_comment = GuardComment(
        guard_id=guard_id,
        comment=comment,
        comment_type=comment_type,
        created_by=session['username']
    )
    
    db.session.add(new_comment)
    db.session.commit()
    
    return jsonify({'success': True, 'message': 'Comment added successfully'})

@app.route('/api/delete-guard-comment/<int:comment_id>', methods=['DELETE'])
def delete_guard_comment(comment_id):
    """Soft delete a guard comment"""
    if 'username' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    comment = GuardComment.query.get_or_404(comment_id)
    
    # 3. Restricted Deletion: Only the original creator can delete the comment
    if comment.created_by != session['username']:
        return jsonify({'error': 'Access denied: Only the comment creator can delete this note.'}), 403
    
    comment.is_active = False
    db.session.commit()
    
    return jsonify({'success': True, 'message': 'Comment deleted'})

@app.route('/api/create-shift-override', methods=['POST'])
def create_shift_override():
    """Create a shift override for a guard"""
    # 1. Enforce Role Check (Only Supervisor/BSO)
    auth_check = check_write_access()
    if auth_check:
        return auth_check
    
    data = request.get_json()
    guard_id = data['guard_id']
    override_shift = data['override_shift']
    override_location_id = data.get('override_location_id')
    reason = data['reason']
    target_date = data.get('date', date.today().isoformat())
    
    if isinstance(target_date, str):
        target_date = datetime.strptime(target_date, '%Y-%m-%d').date()
    
    guard = Guard.query.get_or_404(guard_id)
    
    # Check if override already exists for this date
    existing_override = ShiftOverride.query.filter_by(
        guard_id=guard_id,
        date=target_date,
        is_active=True
    ).first()
    
    if existing_override:
        # Update existing override
        existing_override.override_shift = override_shift
        existing_override.override_location_id = override_location_id or guard.location_id
        existing_override.reason = reason
        existing_override.created_by = session['username']
        existing_override.created_at = datetime.utcnow()
    else:
        # Create new override
        new_override = ShiftOverride(
            guard_id=guard_id,
            original_shift=guard.shift_type,
            override_shift=override_shift,
            original_location_id=guard.location_id,
            override_location_id=override_location_id or guard.location_id,
            date=target_date,
            reason=reason,
            created_by=session['username']
        )
        db.session.add(new_override)
    
    db.session.commit()
    return jsonify({'success': True, 'message': 'Shift override created successfully'})

@app.route('/api/guard-shift-info/<int:guard_id>')
def get_guard_shift_info(guard_id):
    """Get guard's shift information (Requires any authenticated user to view)"""
    # Assuming this view is open to all logged-in staff
    guard = Guard.query.get_or_404(guard_id)
    today = date.today()
    
    override = ShiftOverride.query.filter_by(
        guard_id=guard_id,
        date=today,
        is_active=True
    ).first()
    
    result = {
        'guard_id': guard.id,
        'guard_name': guard.name,
        'default_shift': guard.shift_type,
        'default_location': guard.location.name,
        'default_company': guard.location.company.name,
        'has_override': override is not None
    }
    
    if override:
        result.update({
            'current_shift': override.override_shift,
            'current_location': override.override_location.name if override.override_location else guard.location.name,
            'current_company': override.override_location.company.name if override.override_location else guard.location.company.name,
            'override_reason': override.reason,
            'override_created_by': override.created_by,
            'is_location_changed': override.original_location_id != override.override_location_id,
            'is_shift_changed': override.original_shift != override.override_shift
        })
    else:
        result.update({
            'current_shift': guard.shift_type,
            'current_location': guard.location.name,
            'current_company': guard.location.company.name
        })
    
    return jsonify(result)

@app.route('/api/remove-shift-override/<int:guard_id>', methods=['DELETE'])
def remove_shift_override(guard_id):
    """Remove active shift override for a guard"""
    # 1. Enforce Role Check (Only Supervisor/BSO)
    auth_check = check_write_access()
    if auth_check:
        return auth_check
    
    today = date.today()
    override = ShiftOverride.query.filter_by(
        guard_id=guard_id,
        date=today,
        is_active=True
    ).first()
    
    if not override:
        return jsonify({'error': 'No active override found'}), 404
    
    override.is_active = False
    db.session.commit()
    
    return jsonify({'success': True, 'message': 'Shift override removed'})

@app.route('/api/locations-for-shift/<shift>')
def get_locations_for_shift(shift):
    """Get all accessible locations that have guards for a specific shift (Requires any authenticated user to view)"""
    if 'username' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    locations = db.session.query(Location).join(Guard)\
        .filter(Location.is_accessible == True)\
        .filter(Guard.shift_type == shift)\
        .distinct().all()
    
    result = []
    for location in locations:
        result.append({
            'id': location.id,
            'name': location.name,
            'company': location.company.name
        })
    
    return jsonify(result)

# ============================================================================
# NOTIFICATION API ROUTES
# ============================================================================

@app.route('/api/notifications')
def get_notifications():
    """Get notifications for current user"""
    if 'username' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    username = session['username']
    
    # Get unread notifications first, then recent read ones
    unread = Notification.query.filter_by(
        recipient_username=username,
        is_read=False,
        is_dismissed=False
    ).filter(
        Notification.scheduled_for <= datetime.utcnow()
    ).order_by(Notification.created_at.desc()).limit(20).all()
    
    recent_read = Notification.query.filter_by(
        recipient_username=username,
        is_read=True,
        is_dismissed=False
    ).filter(
        Notification.created_at >= datetime.utcnow() - timedelta(days=7)
    ).order_by(Notification.created_at.desc()).limit(10).all()
    
    all_notifications = unread + recent_read
    
    result = []
    for notification in all_notifications:
        result.append({
            'id': notification.id,
            'title': notification.title,
            'message': notification.message,
            'type': notification.notification_type,
            'category': notification.category,
            'is_read': notification.is_read,
            'created_at': notification.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            'reference_id': notification.reference_id,
            'reference_type': notification.reference_type
        })
    
    return jsonify(result)

@app.route('/api/notifications/count')
def get_notification_count():
    """Get unread notification count"""
    if 'username' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    username = session['username']
    count = Notification.query.filter_by(
        recipient_username=username,
        is_read=False,
        is_dismissed=False
    ).filter(
        Notification.scheduled_for <= datetime.utcnow()
    ).count()
    
    return jsonify({'count': count})

@app.route('/api/notifications/<int:notification_id>/read', methods=['POST'])
def mark_notification_read(notification_id):
    """Mark a notification as read"""
    if 'username' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    notification = Notification.query.get_or_404(notification_id)
    
    # Verify ownership
    if notification.recipient_username != session['username']:
        return jsonify({'error': 'Access denied'}), 403
    
    notification.is_read = True
    notification.delivered_at = datetime.utcnow()
    db.session.commit()
    
    return jsonify({'success': True})

@app.route('/api/notifications/<int:notification_id>/dismiss', methods=['POST'])
def dismiss_notification(notification_id):
    """Dismiss a notification"""
    if 'username' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    notification = Notification.query.get_or_404(notification_id)
    
    # Verify ownership
    if notification.recipient_username != session['username']:
        return jsonify({'error': 'Access denied'}), 403
    
    notification.is_dismissed = True
    db.session.commit()
    
    return jsonify({'success': True})

@app.route('/api/notifications/mark-all-read', methods=['POST'])
def mark_all_notifications_read():
    """Mark all notifications as read for current user"""
    if 'username' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    username = session['username']
    notifications = Notification.query.filter_by(
        recipient_username=username,
        is_read=False
    ).all()
    
    for notification in notifications:
        notification.is_read = True
        notification.delivered_at = datetime.utcnow()
    
    db.session.commit()
    
    return jsonify({'success': True, 'count': len(notifications)})

# ============================================================================
# REPORTS ROUTES  
# ============================================================================

@app.route('/reports')
def reports_page():
    """Reports dashboard"""
    if 'username' not in session:
        return redirect(url_for('login'))
    
    # Only certain roles can access reports
    allowed_roles = ['Ops Manager', 'Administrator', 'Business Support Officer', 'HR Officer', 'Finance', 'General Manager']
    if session.get('role') not in allowed_roles:
        flash('Access denied - insufficient permissions', 'error')
        return redirect(url_for('dashboard'))
    
    return render_template('reports.html')

@app.route('/generate-report/<report_type>')
def generate_report(report_type):
    """Generate and download reports"""
    if 'username' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    allowed_roles = ['Ops Manager', 'HR Officer', 'Finance', 'General Manager']
    if session.get('role') not in allowed_roles:
        return jsonify({'error': 'Access denied'}), 403
    
    try:
        # ⭐ Corrected line: Initialize ReportGenerator with required arguments ⭐
        generator = ReportGenerator(db, Guard, Attendance, Location, Company)
        
        report_format = request.args.get('format', 'pdf')
        
        if report_type == 'daily-attendance':
            report_date = request.args.get('date')
            if report_date:
                report_date = datetime.strptime(report_date, '%Y-%m-%d').date()
            
            buffer = generator.generate_daily_attendance_report(report_date, report_format)
            filename = f"daily_attendance_{report_date or date.today().strftime('%Y-%m-%d')}.{report_format}"
            
        elif report_type == 'weekly-attendance':
            start_date = request.args.get('start_date')
            if start_date:
                start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
            
            buffer = generator.generate_weekly_attendance_report(start_date, report_format)
            filename = f"weekly_attendance_{start_date or (date.today() - timedelta(days=7)).strftime('%Y-%m-%d')}.{report_format}"
            
        elif report_type == 'guard-performance':
            days = int(request.args.get('days', 30))
            buffer = generator.generate_guard_performance_report(days, report_format)
            filename = f"guard_performance_{days}days.{report_format}"
            
        elif report_type == 'location-analysis':
            days = int(request.args.get('days', 30))
            buffer = generator.generate_location_analysis_report(days, report_format)
            filename = f"location_analysis_{days}days.{report_format}"
            
        else:
            return jsonify({'error': 'Invalid report type'}), 400
        
        # Create response
        response = make_response(buffer.getvalue())
        
        if report_format == 'pdf':
            response.headers['Content-Type'] = 'application/pdf'
        elif report_format == 'csv':
            response.headers['Content-Type'] = 'text/csv'
        
        response.headers['Content-Disposition'] = f'attachment; filename={filename}'
        
        return response
        
    except Exception as e:
        return jsonify({'error': f'Report generation failed: {str(e)}'}), 500
# ============================================================================
# API ROUTES FOR REQUESTS
# ============================================================================

@app.route('/api/edit-request/<int:request_id>', methods=['PUT'])
def edit_request(request_id):
    """Edit an existing request record"""
    if 'username' not in session:
        return jsonify({'error': 'Not authenticated'}), 401

    try:
        req = Request.query.get_or_404(request_id)
        data = request.get_json()

        new_type = data.get('type')
        new_description = data.get('description')

        if new_type:
            req.type = new_type
        if new_description:
            req.description = new_description

        req.updated_by = session.get('username')
        req.responded_at = datetime.now()
        
        db.session.commit()
        return jsonify({'success': True, 'message': 'Request updated successfully'}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': 'Internal server error', 'message': str(e)}), 500

# ============================================================================
# API ROUTES FOR REQUESTS
# ============================================================================

... # Your existing edit-request route should be here

@app.route('/api/delete-request/<int:request_id>', methods=['DELETE'])
def delete_request(request_id):
    """Deletes a request from the database."""
    # Ensure the user is authenticated
    if 'username' not in session:
        return jsonify({'error': 'Not authenticated'}), 401

    req = Request.query.get_or_404(request_id)
    
    # Optional: Add a check to ensure only the creator can delete it
    if session.get('username') != req.from_user:
        return jsonify({'error': 'You can only delete your own requests'}), 403

    try:
        db.session.delete(req)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Request deleted successfully'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': 'Failed to delete request', 'details': str(e)}), 500

@app.route('/admin/migrate-database')
def migrate_database():
    """One-time migration to add new fields"""
    if 'username' not in session or session.get('role') != 'Administrator':
        return "Access denied"
    
    try:
        # Add new columns if they don't exist
        with db.engine.connect() as conn:
            conn.execute(db.text("ALTER TABLE guard ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE"))
            conn.execute(db.text("ALTER TABLE guard ADD COLUMN IF NOT EXISTS resigned_date DATE"))
            conn.execute(db.text("ALTER TABLE guard ADD COLUMN IF NOT EXISTS notes TEXT"))
            conn.commit()
        
        return "Database migrated successfully! You can now use the admin features."
    except Exception as e:
        return f"Migration error: {str(e)}"
    
# ============================================================================
# ATTENDANCE DELETION API ROUTES (Add to app.py after admin routes)
# ============================================================================

@app.route('/api/admin/attendance/<int:attendance_id>', methods=['DELETE'])
def admin_delete_attendance(attendance_id):
    """Soft delete an attendance record (Administrator only)"""
    if 'username' not in session or session.get('role') != 'Administrator':
        return jsonify({'error': 'Access denied - Administrator privileges required'}), 403
    
    attendance = Attendance.query.get_or_404(attendance_id)
    data = request.get_json() or {}
    
    try:
        # Create backup in DeletedAttendance table
        deleted_record = DeletedAttendance(
            original_attendance_id=attendance.id,
            guard_id=attendance.guard_id,
            date=attendance.date,
            shift=attendance.shift,
            status=attendance.status,
            notes=attendance.notes,
            marked_by=attendance.marked_by,
            timestamp=attendance.timestamp,
            deleted_by=session['username'],
            deletion_reason=data.get('reason', 'No reason provided')
        )
        
        db.session.add(deleted_record)
        
        # Delete the original record
        db.session.delete(attendance)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Attendance record deleted successfully',
            'deleted_id': deleted_record.id,
            'can_undo': True
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Failed to delete record: {str(e)}'}), 500


@app.route('/api/admin/attendance/undo/<int:deleted_id>', methods=['POST'])
def admin_undo_delete_attendance(deleted_id):
    """Restore a deleted attendance record (Administrator only)"""
    if 'username' not in session or session.get('role') != 'Administrator':
        return jsonify({'error': 'Access denied - Administrator privileges required'}), 403
    
    deleted_record = DeletedAttendance.query.get_or_404(deleted_id)
    
    try:
        # Restore the attendance record
        restored_attendance = Attendance(
            guard_id=deleted_record.guard_id,
            date=deleted_record.date,
            shift=deleted_record.shift,
            status=deleted_record.status,
            notes=deleted_record.notes,
            marked_by=deleted_record.marked_by,
            timestamp=deleted_record.timestamp
        )
        
        db.session.add(restored_attendance)
        
        # Remove from deleted records
        db.session.delete(deleted_record)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Attendance record restored successfully',
            'restored_id': restored_attendance.id
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Failed to restore record: {str(e)}'}), 500


@app.route('/api/admin/attendance/deleted')
def admin_get_deleted_attendance():
    """Get recently deleted attendance records for undo (Administrator only)"""
    if 'username' not in session or session.get('role') != 'Administrator':
        return jsonify({'error': 'Access denied'}), 403
    
    # Get records deleted in last 24 hours
    cutoff_time = datetime.utcnow() - timedelta(hours=24)
    deleted_records = DeletedAttendance.query.filter(
        DeletedAttendance.deleted_at >= cutoff_time
    ).order_by(DeletedAttendance.deleted_at.desc()).all()
    
    result = []
    for record in deleted_records:
        result.append({
            'id': record.id,
            'guard_name': record.guard.name,
            'location': record.guard.location.name,
            'date': record.date.strftime('%Y-%m-%d'),
            'shift': record.shift,
            'status': record.status,
            'deleted_by': record.deleted_by,
            'deleted_at': record.deleted_at.strftime('%Y-%m-%d %H:%M:%S'),
            'reason': record.deletion_reason
        })
    
    return jsonify(result)


# ============================================================================
# MONTHLY NOMINAL ROLL REPORT (Add to reports.py or app.py)
# ============================================================================

@app.route('/generate-report/monthly-nominal-roll')
def generate_monthly_nominal_roll():
    """Generate monthly nominal roll report"""
    if 'username' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    allowed_roles = ['Ops Manager', 'HR Officer', 'Finance', 'General Manager', 'Administrator', 'Business Support Officer']
    if session.get('role') not in allowed_roles:
        return jsonify({'error': 'Access denied'}), 403
    
    try:
        # Get parameters
        year = int(request.args.get('year', datetime.now().year))
        month = int(request.args.get('month', datetime.now().month))
        company_filter = request.args.get('company', '')
        report_format = request.args.get('format', 'pdf')
        
        # Create date range for the month
        start_date = date(year, month, 1)
        if month == 12:
            end_date = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            end_date = date(year, month + 1, 1) - timedelta(days=1)
        
        # Query all guards with their attendance for the month
        query = db.session.query(Guard, Location, Company).join(
            Location, Guard.location_id == Location.id
        ).join(
            Company, Location.company_id == Company.id
        )
        
        if company_filter:
            query = query.filter(Company.name == company_filter)
        
        guards = query.order_by(Company.name, Location.name, Guard.name).all()
        
        # Build nominal roll data
        nominal_roll = []
        for guard, location, company in guards:
            # Get attendance records for this guard in the month
            attendance_records = Attendance.query.filter(
                Attendance.guard_id == guard.id,
                Attendance.date >= start_date,
                Attendance.date <= end_date
            ).all()
            
            # Count attendance
            present_count = sum(1 for a in attendance_records if a.status == 'present')
            absent_count = sum(1 for a in attendance_records if a.status == 'absent')
            off_count = sum(1 for a in attendance_records if a.status == 'off')
            leave_count = sum(1 for a in attendance_records if a.status == 'leave')
            total_days = len(attendance_records)
            
            # Calculate attendance percentage
            if total_days > 0:
                attendance_percentage = (present_count / total_days) * 100
            else:
                attendance_percentage = 0
            
            nominal_roll.append({
                'guard_name': guard.name,
                'role': guard.role,
                'location': location.name,
                'company': company.name,
                'shift': guard.shift_type,
                'present': present_count,
                'absent': absent_count,
                'off': off_count,
                'leave': leave_count,
                'total_days': total_days,
                'attendance_percentage': round(attendance_percentage, 2)
            })
        
        # Generate report based on format
        if report_format == 'csv':
            return generate_nominal_roll_csv(nominal_roll, year, month, company_filter)
        else:
            return generate_nominal_roll_pdf(nominal_roll, year, month, company_filter)
            
    except Exception as e:
        return jsonify({'error': f'Report generation failed: {str(e)}'}), 500


def generate_nominal_roll_csv(data, year, month, company_filter):
    """Generate CSV format for nominal roll"""
    import io
    import csv
    from calendar import month_name
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Header
    title = f"Monthly Nominal Roll - {month_name[month]} {year}"
    if company_filter:
        title += f" - {company_filter}"
    
    writer.writerow([title])
    writer.writerow([])
    writer.writerow(['Guard Name', 'Role', 'Location', 'Company', 'Shift', 
                     'Present', 'Absent', 'Off', 'Leave', 'Total Days', 'Attendance %'])
    
    # Data rows
    for record in data:
        writer.writerow([
            record['guard_name'],
            record['role'].title(),
            record['location'],
            record['company'],
            record['shift'].title(),
            record['present'],
            record['absent'],
            record['off'],
            record['leave'],
            record['total_days'],
            f"{record['attendance_percentage']}%"
        ])
    
    # Summary
    writer.writerow([])
    writer.writerow(['Summary'])
    writer.writerow(['Total Guards', len(data)])
    writer.writerow(['Total Present Days', sum(r['present'] for r in data)])
    writer.writerow(['Total Absent Days', sum(r['absent'] for r in data)])
    
    # Create response
    output.seek(0)
    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv'
    response.headers['Content-Disposition'] = f'attachment; filename=nominal_roll_{month}_{year}.csv'
    
    return response


def generate_nominal_roll_pdf(data, year, month, company_filter):
    """Generate PDF format for nominal roll"""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    from calendar import month_name
    import io
    
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4))
    elements = []
    styles = getSampleStyleSheet()
    
    # Title
    title = f"Monthly Nominal Roll - {month_name[month]} {year}"
    if company_filter:
        title += f" - {company_filter}"
    
    elements.append(Paragraph(title, styles['Title']))
    elements.append(Spacer(1, 0.3*inch))
    
    # Table data
    table_data = [
        ['Guard Name', 'Role', 'Location', 'Company', 'Shift', 
         'Present', 'Absent', 'Off', 'Leave', 'Total', 'Attendance %']
    ]
    
    for record in data:
        table_data.append([
            record['guard_name'],
            record['role'].title(),
            record['location'],
            record['company'],
            record['shift'].title(),
            str(record['present']),
            str(record['absent']),
            str(record['off']),
            str(record['leave']),
            str(record['total_days']),
            f"{record['attendance_percentage']}%"
        ])
    
    # Create table
    table = Table(table_data)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
    ]))
    
    elements.append(table)
    elements.append(Spacer(1, 0.3*inch))
    
    # Summary
    summary_data = [
        ['Summary'],
        ['Total Guards:', str(len(data))],
        ['Total Present Days:', str(sum(r['present'] for r in data))],
        ['Total Absent Days:', str(sum(r['absent'] for r in data))],
    ]
    
    summary_table = Table(summary_data)
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightblue),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ]))
    
    elements.append(summary_table)
    
    # Build PDF
    doc.build(elements)
    buffer.seek(0)
    
    # Create response
    response = make_response(buffer.getvalue())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename=nominal_roll_{month}_{year}.pdf'
    
    return response
    
# ============================================================================
# APPLICATION STARTUP
# ============================================================================

# Function to initialize database (for running migrations/setup)
def create_db_tables():
    """Initializes all database tables and seeds initial users if the database is empty."""
    with app.app_context():
        # 1. Create all tables
        db.create_all()
        print("Database tables created successfully.")
        
        # 2. Seed initial users if none exist
        users_added_or_updated = 0
        for username, password, role in DEFAULT_USERS:
            # Try to find the user by username
            user = User.query.filter_by(username=username).first()

            if user:
                # User exists: Update the password and role to ensure they match the latest code
                user.set_password(password)
                user.role = role
                db.session.add(user)
                print(f"UPSERT: Updated password for existing user: {username}")
                users_added_or_updated += 1
            else:
                # User does not exist: Create and save the new user
                new_user = User(username=username, role=role)
                new_user.set_password(password)
                db.session.add(new_user)
                print(f"UPSERT: Created new user: {username}")
                users_added_or_updated += 1
            
        if users_added_or_updated > 0:
            db.session.commit()
            print(f"--- Successfully completed data UPSERT: {users_added_or_updated} users checked/updated. ---")
        else:
            print("--- No default users to upsert. ---")

# --- Utility Functions ---

def requires_role(required_role):
    """Decorator to enforce role-based access control."""
    def wrapper(f):
        @login_required
        def decorated_function(*args, **kwargs):
            if current_user.role != required_role and current_user.role != 'admin':
                flash('You do not have the required permissions to access this page.', 'danger')
                return redirect(url_for('home'))
            return f(*args, **kwargs)
        return decorated_function
    return wrapper



if __name__ == '__main__':
    init_database()
    print("✅ PANOS Security System initialized!")
    print("🏢 Database created with sample data")
    print("👥 Users created for all roles")
    print("📍 Locations and guards populated")
    print("🔐 Login with: supervisor/sup2025, ops/ops2025, hr/hr2025, etc.")

    with app.app_context():
        db.create_all()
        print("Database tables created successfully.")
    
    app.run(debug=True, port=5000)