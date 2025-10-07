from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime, date, UTC

# Initialize db here - will be bound to app later
db = SQLAlchemy()

# ============================================================================
# DATABASE MODELS
# ============================================================================

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)  # bcrypt hash
    role = db.Column(db.String(50), nullable=False, default='Employee')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))

    def set_password(self, password, bcrypt):
        """Hashes and sets the password."""
        self.password = bcrypt.generate_password_hash(password).decode('utf-8')

    def check_password(self, password, bcrypt):
        """Checks the provided password against the stored hash."""
        return bcrypt.check_password_hash(self.password, password)

    def __repr__(self):
        return f"User('{self.username}', '{self.role}')"


class Company(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    locations = db.relationship('Location', backref='company', lazy=True)


class Location(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=False)
    is_accessible = db.Column(db.Boolean, default=True)
    guards = db.relationship('Guard', backref='location', lazy=True)


class Guard(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    location_id = db.Column(db.Integer, db.ForeignKey('location.id'), nullable=False)
    shift_type = db.Column(db.String(10), nullable=False)  # 'day' or 'night'
    role = db.Column(db.String(20), default='guard')  # 'guard', 'supervisor', 'driver'
    is_active = db.Column(db.Boolean, default=True)
    resigned_date = db.Column(db.Date, nullable=True)
    notes = db.Column(db.Text, nullable=True)


class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    guard_id = db.Column(db.Integer, db.ForeignKey('guard.id'), nullable=False)
    date = db.Column(db.Date, default=date.today)
    shift = db.Column(db.String(10), nullable=False)
    status = db.Column(db.String(20))  # 'present', 'absent', 'off', 'leave'
    notes = db.Column(db.Text)
    marked_by = db.Column(db.String(50))
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(UTC))


class DeletedAttendance(db.Model):
    """Soft-deleted attendance records that can be restored"""
    id = db.Column(db.Integer, primary_key=True)
    original_attendance_id = db.Column(db.Integer, nullable=False)
    guard_id = db.Column(db.Integer, db.ForeignKey('guard.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    shift = db.Column(db.String(10), nullable=False)
    status = db.Column(db.String(20), nullable=False)
    notes = db.Column(db.Text)
    marked_by = db.Column(db.String(50))
    timestamp = db.Column(db.DateTime, nullable=False)
    deleted_by = db.Column(db.String(50), nullable=False)
    deleted_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    deletion_reason = db.Column(db.Text)
    
    # Relationships
    guard = db.relationship('Guard', backref='deleted_attendance_records')


class GuardComment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    guard_id = db.Column(db.Integer, db.ForeignKey('guard.id'), nullable=False)
    comment = db.Column(db.Text, nullable=False)
    comment_type = db.Column(db.String(50), default='note')
    created_by = db.Column(db.String(50), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    is_active = db.Column(db.Boolean, default=True)
    
    guard = db.relationship('Guard', backref='comments')


class ShiftOverride(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    guard_id = db.Column(db.Integer, db.ForeignKey('guard.id'), nullable=False)
    original_shift = db.Column(db.String(10), nullable=False)
    override_shift = db.Column(db.String(10), nullable=False)
    original_location_id = db.Column(db.Integer, db.ForeignKey('location.id'))
    override_location_id = db.Column(db.Integer, db.ForeignKey('location.id'))
    date = db.Column(db.Date, default=date.today)
    reason = db.Column(db.String(200), nullable=False)
    created_by = db.Column(db.String(50), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    is_active = db.Column(db.Boolean, default=True)
    
    guard = db.relationship('Guard', backref='shift_overrides')
    original_location = db.relationship('Location', foreign_keys=[original_location_id])
    override_location = db.relationship('Location', foreign_keys=[override_location_id])


class PayrollTracking(db.Model):
    """Track all attendance events for payroll calculations"""
    id = db.Column(db.Integer, primary_key=True)
    guard_id = db.Column(db.Integer, db.ForeignKey('guard.id'), nullable=False)
    date = db.Column(db.Date, default=date.today)
    scheduled_shift = db.Column(db.String(10), nullable=False)
    actual_shift = db.Column(db.String(10), nullable=False)
    scheduled_location_id = db.Column(db.Integer, db.ForeignKey('location.id'))
    actual_location_id = db.Column(db.Integer, db.ForeignKey('location.id'))
    status = db.Column(db.String(20), nullable=False)
    hours_worked = db.Column(db.Float, default=0.0)
    is_overtime = db.Column(db.Boolean, default=False)
    is_shift_differential = db.Column(db.Boolean, default=False)
    is_location_premium = db.Column(db.Boolean, default=False)
    base_rate = db.Column(db.Float, default=0.0)
    total_pay = db.Column(db.Float, default=0.0)
    created_by = db.Column(db.String(50), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    
    guard = db.relationship('Guard', backref='payroll_records')
    scheduled_location = db.relationship('Location', foreign_keys=[scheduled_location_id])
    actual_location = db.relationship('Location', foreign_keys=[actual_location_id])


class NotificationSettings(db.Model):
    """User notification preferences"""
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), nullable=False, unique=True)
    role = db.Column(db.String(50), nullable=False)
    
    day_shift_reminder_time = db.Column(db.String(5), default='09:00')
    night_shift_reminder_time = db.Column(db.String(5), default='19:00')
    overdue_reminder_minutes = db.Column(db.Integer, default=30)
    urgent_reminder_minutes = db.Column(db.Integer, default=120)
    
    notify_new_requests = db.Column(db.Boolean, default=True)
    notify_attendance_submitted = db.Column(db.Boolean, default=True)
    notify_attendance_missing = db.Column(db.Boolean, default=True)
    notify_guard_issues = db.Column(db.Boolean, default=True)
    notify_shift_changes = db.Column(db.Boolean, default=True)
    
    in_app_notifications = db.Column(db.Boolean, default=True)
    email_notifications = db.Column(db.Boolean, default=False)
    
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))


class Notification(db.Model):
    """Individual notifications"""
    id = db.Column(db.Integer, primary_key=True)
    recipient_username = db.Column(db.String(50), nullable=False)
    recipient_role = db.Column(db.String(50), nullable=False)
    
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=False)
    notification_type = db.Column(db.String(50), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    
    reference_id = db.Column(db.Integer)
    reference_type = db.Column(db.String(50))
    
    is_read = db.Column(db.Boolean, default=False)
    is_dismissed = db.Column(db.Boolean, default=False)
    
    scheduled_for = db.Column(db.DateTime)
    delivered_at = db.Column(db.DateTime)
    
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    expires_at = db.Column(db.DateTime)


class AttendanceDeadline(db.Model):
    """Track attendance submission deadlines"""
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    shift = db.Column(db.String(10), nullable=False)
    
    expected_submission_time = db.Column(db.DateTime, nullable=False)
    actual_submission_time = db.Column(db.DateTime)
    
    is_submitted = db.Column(db.Boolean, default=False)
    is_overdue = db.Column(db.Boolean, default=False)
    
    reminder_30min_sent = db.Column(db.Boolean, default=False)
    reminder_2hour_sent = db.Column(db.Boolean, default=False)
    
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))


class Request(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    from_user = db.Column(db.String(50), nullable=False)
    role = db.Column(db.String(50), nullable=False)
    type = db.Column(db.String(50), nullable=False)
    description = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default='Pending')
    submitted_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    responded_at = db.Column(db.DateTime)
    updated_by = db.Column(db.String(50))