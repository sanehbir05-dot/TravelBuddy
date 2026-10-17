"""Travel Buddy: verified college students find companions on the same journey."""
import os
import re
import secrets
import json
import smtplib
import urllib.request
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo
from email.message import EmailMessage
from email.utils import parseaddr
from functools import wraps

import click
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, flash, session, abort
from flask_sqlalchemy import SQLAlchemy
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_migrate import Migrate
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from sqlalchemy import or_, and_
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash

from colleges import INDIAN_COLLEGES  # noqa: F401  (full list kept for a later multi-college launch)

load_dotenv()

app = Flask(__name__)
IS_PRODUCTION = os.environ.get('APP_ENV', '').lower() == 'production'
SECRET_KEY = os.environ.get('SECRET_KEY', '')
if IS_PRODUCTION and len(SECRET_KEY) < 32:
    raise RuntimeError('Set SECRET_KEY to a long random value (at least 32 characters) in production.')
app.config['SECRET_KEY'] = SECRET_KEY or 'dev-secret-key-change-in-production'


def database_url():
    url = os.environ.get('DATABASE_URL', 'sqlite:///travelbuddy_v2.db')
    # Hosts hand out postgres:// or postgresql:// URLs; use the psycopg 3 driver for both.
    for prefix in ('postgres://', 'postgresql://'):
        if url.startswith(prefix):
            return 'postgresql+psycopg://' + url[len(prefix):]
    return url


app.config['SQLALCHEMY_DATABASE_URI'] = database_url()
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'pool_pre_ping': True}
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['CSRF_ENABLED'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['REMEMBER_COOKIE_HTTPONLY'] = True
app.config['REMEMBER_COOKIE_DURATION'] = timedelta(days=30)
if IS_PRODUCTION:
    app.config['SESSION_COOKIE_SECURE'] = True
    app.config['REMEMBER_COOKIE_SECURE'] = True
    app.config['PREFERRED_URL_SCHEME'] = 'https'
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# Everyone using Travel Buddy is in India, and the server clock is UTC.
APP_TZ = ZoneInfo(os.environ.get('APP_TIMEZONE', 'Asia/Kolkata'))


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def local_today():
    return datetime.now(APP_TZ).date()

# Matching knobs
TIME_WINDOW_HOURS = int(os.environ.get('MATCH_TIME_WINDOW_HOURS', 3))
OTP_TTL_MINUTES = 10
OTP_MAX_ATTEMPTS = 5
AUTO_HIDE_REPORT_THRESHOLD = 3  # distinct reporters before a profile is hidden pending admin review

# Launch campus and the email domain(s) that prove a student belongs to it.
# Add campuses later with CAMPUSES="A|B" and ALLOWED_EMAIL_DOMAINS="thapar.edu,other.edu.in".
CAMPUSES = [c.strip() for c in os.environ.get('CAMPUSES', 'Thapar Institute of Engineering & Technology (TIET), Patiala').split('|') if c.strip()]
ALLOWED_EMAIL_DOMAINS = [d.strip().lower().lstrip('@') for d in os.environ.get('ALLOWED_EMAIL_DOMAINS', 'thapar.edu').split(',') if d.strip()]
EMAIL_HINT = ' or '.join('@' + d for d in ALLOWED_EMAIL_DOMAINS)
ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get('ADMIN_EMAILS', '').split(',') if e.strip()}

# Email (optional). Without BREVO_API_KEY or MAIL_USERNAME the verification code is shown on screen (dev mode).
# Render's free plan blocks SMTP ports, so online use BREVO_API_KEY (sent over HTTPS). SMTP is for local or paid hosts.
BREVO_API_KEY = os.environ.get('BREVO_API_KEY', '')
MAIL_SERVER = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
MAIL_PORT = int(os.environ.get('MAIL_PORT', 587))
MAIL_USERNAME = os.environ.get('MAIL_USERNAME', '')
MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD', '')
MAIL_FROM = os.environ.get('MAIL_FROM', MAIL_USERNAME)  # e.g. "Travel Buddy <hello@yourdomain.in>"
EMAIL_ENABLED = bool(BREVO_API_KEY or MAIL_USERNAME)
# Render sets RENDER_EXTERNAL_URL itself, so SITE_URL is only needed for a custom domain.
SITE_URL = (os.environ.get('SITE_URL') or os.environ.get('RENDER_EXTERNAL_URL') or 'http://127.0.0.1:5000').rstrip('/')

# Google OAuth (optional)
GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', '')
google = None
if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
    from authlib.integrations.flask_client import OAuth
    oauth = OAuth(app)
    google = oauth.register(
        name='google',
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile'},
    )

db = SQLAlchemy(app)
migrate = Migrate(app, db)
limiter = Limiter(get_remote_address, app=app, default_limits=[],
                  enabled=os.environ.get('RATELIMIT_ENABLED', '1') != '0',
                  storage_uri=os.environ.get('RATELIMIT_STORAGE_URI', 'memory://'))
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

TRANSPORT_MODES = {'bus': 'Bus', 'train': 'Train', 'plane': 'Flight', 'car': 'Car / cab share', 'other': 'Other'}
TRANSPORT_ICONS = {'bus': 'bi-bus-front', 'train': 'bi-train-front', 'plane': 'bi-airplane', 'car': 'bi-car-front', 'other': 'bi-signpost'}
REPORT_REASONS = ['Harassment or inappropriate messages', 'Fake profile / not a student',
                  'Made me feel unsafe', 'Spam', 'Other']


# ───────────────────────── Models ─────────────────────────
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=True)
    google_id = db.Column(db.String(100), unique=True, nullable=True)
    gender = db.Column(db.String(10), nullable=False, default='')
    college = db.Column(db.String(200), nullable=False, default='')
    batch = db.Column(db.String(50), nullable=False, default='')
    contact_info = db.Column(db.String(100), nullable=False, default='')
    is_verified = db.Column(db.Boolean, default=False)
    is_suspended = db.Column(db.Boolean, default=False)
    is_deleted = db.Column(db.Boolean, default=False)
    notify_by_email = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    trips = db.relationship('Trip', backref='user', lazy=True)
    sent_requests = db.relationship('CompanionRequest', foreign_keys='CompanionRequest.sender_id', backref='sender', lazy=True)
    received_requests = db.relationship('CompanionRequest', foreign_keys='CompanionRequest.receiver_id', backref='receiver', lazy=True)

    @property
    def profile_complete(self):
        return bool(self.gender and self.college and self.batch)

    @property
    def is_admin(self):
        return self.email.lower() in ADMIN_EMAILS

    @property
    def public_name(self):
        """First name + last initial. Full names are only shown to accepted companions."""
        parts = self.name.split()
        if len(parts) > 1:
            return f"{parts[0]} {parts[-1][0]}."
        return parts[0] if parts else 'Student'


class Trip(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    origin = db.Column(db.String(100), nullable=False)
    destination = db.Column(db.String(100), nullable=False)
    date = db.Column(db.String(10), nullable=False)   # YYYY-MM-DD
    time = db.Column(db.String(5), nullable=False)    # HH:MM, approximate departure
    transport_mode = db.Column(db.String(20), nullable=False)
    preferences = db.Column(db.String(300), nullable=False, default='')
    female_only_preference = db.Column(db.Boolean, default=False)
    is_cancelled = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=utcnow)

    @property
    def departs_at(self):
        return datetime.strptime(f"{self.date} {self.time}", '%Y-%m-%d %H:%M')

    @property
    def is_upcoming(self):
        return not self.is_cancelled and self.date >= local_today().isoformat()


class CompanionRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    trip_id = db.Column(db.Integer, db.ForeignKey('trip.id'), nullable=False)             # receiver's trip
    sender_trip_id = db.Column(db.Integer, db.ForeignKey('trip.id'), nullable=True)       # sender's own matching trip
    note = db.Column(db.String(300), nullable=False, default='')
    status = db.Column(db.String(20), default='pending')  # pending / accepted / declined / cancelled
    shared_contact = db.Column(db.Boolean, default=False)            # sender shares contact
    receiver_shared_contact = db.Column(db.Boolean, default=False)   # receiver shares contact
    created_at = db.Column(db.DateTime, default=utcnow)
    trip = db.relationship('Trip', foreign_keys=[trip_id], backref='requests', lazy=True)
    sender_trip = db.relationship('Trip', foreign_keys=[sender_trip_id], lazy=True)

    def other(self, user):
        return self.receiver if user.id == self.sender_id else self.sender

    def contact_shared_by(self, user):
        return self.shared_contact if user.id == self.sender_id else self.receiver_shared_contact


class ChatMessage(db.Model):
    __tablename__ = 'message'
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    companion_request_id = db.Column(db.Integer, db.ForeignKey('companion_request.id'), nullable=False)
    message = db.Column(db.Text, nullable=False)
    sent_at = db.Column(db.DateTime, default=utcnow)
    sender = db.relationship('User', foreign_keys=[sender_id])


class OTP(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), nullable=False, index=True)
    code = db.Column(db.String(6), nullable=False)
    attempts = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=utcnow)


class Block(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    blocker_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    blocked_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)
    blocked = db.relationship('User', foreign_keys=[blocked_id])
    __table_args__ = (db.UniqueConstraint('blocker_id', 'blocked_id'),)


class Report(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reporter_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    reported_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    reason = db.Column(db.String(100), nullable=False)
    details = db.Column(db.Text, nullable=False, default='')
    status = db.Column(db.String(20), default='open')  # open / dismissed / actioned
    created_at = db.Column(db.DateTime, default=utcnow)
    reporter = db.relationship('User', foreign_keys=[reporter_id])
    reported = db.relationship('User', foreign_keys=[reported_id])


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# ───────────────────────── Security helpers ─────────────────────────
def csrf_token():
    if '_csrf' not in session:
        session['_csrf'] = secrets.token_hex(16)
    return session['_csrf']


app.jinja_env.globals['csrf_token'] = csrf_token
app.jinja_env.globals['TRANSPORT_MODES'] = TRANSPORT_MODES
app.jinja_env.globals['TRANSPORT_ICONS'] = TRANSPORT_ICONS
app.jinja_env.globals['EMAIL_HINT'] = EMAIL_HINT
app.jinja_env.globals['EMAIL_EXAMPLE'] = 'name@' + ALLOWED_EMAIL_DOMAINS[0]
app.jinja_env.globals['google_enabled'] = lambda: google is not None


@app.before_request
def protect_forms():
    if request.method == 'POST' and app.config['CSRF_ENABLED']:
        sent = request.form.get('csrf_token', '')
        if not sent or not secrets.compare_digest(sent, session.get('_csrf', '')):
            abort(400, 'Form expired. Go back, refresh and try again.')
    if current_user.is_authenticated and current_user.is_suspended and request.endpoint not in ('logout', 'static', 'index'):
        logout_user()
        flash('Your account is suspended while reports are reviewed.')
        return redirect(url_for('index'))


def profile_required(view):
    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if not current_user.profile_complete:
            return redirect(url_for('setup_profile'))
        return view(*args, **kwargs)
    return wrapper


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)
    return wrapper


def is_student_email(email):
    domain = email.split('@')[-1].lower() if '@' in email else ''
    return domain in ALLOWED_EMAIL_DOMAINS


def send_email_brevo(to, subject, body):
    name, addr = parseaddr(MAIL_FROM)
    payload = {'sender': {'name': name or 'Travel Buddy', 'email': addr},
               'to': [{'email': to}], 'subject': subject, 'textContent': body}
    req = urllib.request.Request('https://api.brevo.com/v3/smtp/email', data=json.dumps(payload).encode(),
                                 headers={'api-key': BREVO_API_KEY, 'content-type': 'application/json',
                                          'accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=15):
        pass


def send_email_smtp(to, subject, body):
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = MAIL_FROM
    msg['To'] = to
    msg.set_content(body)
    with smtplib.SMTP(MAIL_SERVER, MAIL_PORT, timeout=15) as smtp:
        smtp.starttls()
        smtp.login(MAIL_USERNAME, MAIL_PASSWORD)
        smtp.send_message(msg)


def send_email(to, subject, body):
    """Send a plain-text email. Returns False when email is not configured or sending failed."""
    if not EMAIL_ENABLED:
        print(f"[dev] Email to {to}: {subject}\n{body}")
        return False
    try:
        (send_email_brevo if BREVO_API_KEY else send_email_smtp)(to, subject, body)
    except Exception as e:  # noqa: BLE001
        app.logger.error('Error sending email to %s: %s', to, e)
        return False
    return True


def send_otp(email, purpose='verify'):
    """Create a 6-digit code. Emails it when SMTP is configured; otherwise returns it for on-screen dev display."""
    code = f"{secrets.randbelow(1000000):06d}"
    OTP.query.filter_by(email=email).delete()
    db.session.add(OTP(email=email, code=code))
    db.session.commit()

    if not EMAIL_ENABLED:
        print(f"[dev] Travel Buddy {purpose} code for {email}: {code}")
        return code
    what = 'reset your password' if purpose == 'reset' else 'verify your email'
    ok = send_email(email, f'Travel Buddy code: {code}',
                    f'Your code to {what} on Travel Buddy is {code}.\n\n'
                    f'It expires in {OTP_TTL_MINUTES} minutes. If you did not ask for it, you can ignore this email.')
    return '' if ok else None


def notify(user, subject, body):
    """Email a user about activity on their account, if they have notifications on."""
    if user.notify_by_email and not user.is_deleted:
        send_email(user.email, subject, f'{body}\n\n{SITE_URL}/dashboard\n\n'
                   'You can turn these emails off on your Travel Buddy profile.')


def check_otp(email, entered):
    otp = OTP.query.filter_by(email=email).first()
    if not otp:
        return 'No code found. Request a new one.'
    if utcnow() - otp.created_at > timedelta(minutes=OTP_TTL_MINUTES):
        return 'That code has expired. Request a new one.'
    if otp.attempts >= OTP_MAX_ATTEMPTS:
        return 'Too many wrong attempts. Request a new code.'
    if not secrets.compare_digest(otp.code, entered):
        otp.attempts += 1
        db.session.commit()
        return 'Wrong code. Try again.'
    db.session.delete(otp)
    db.session.commit()
    return None


# ───────────────────────── Visibility & matching ─────────────────────────
def blocked_ids(user):
    """Users hidden from `user`: those they blocked and those who blocked them."""
    rows = Block.query.filter(or_(Block.blocker_id == user.id, Block.blocked_id == user.id)).all()
    return {r.blocked_id if r.blocker_id == user.id else r.blocker_id for r in rows}


def is_blocked_between(a, b):
    return Block.query.filter(or_(and_(Block.blocker_id == a.id, Block.blocked_id == b.id),
                                  and_(Block.blocker_id == b.id, Block.blocked_id == a.id))).first() is not None


def visible_trips_query(viewer):
    """Upcoming trips by other students of the viewer's college that the viewer is allowed to see."""
    q = Trip.query.join(User, Trip.user_id == User.id).filter(
        Trip.user_id != viewer.id,
        Trip.is_cancelled.is_(False),
        Trip.date >= local_today().isoformat(),
        User.college == viewer.college,
        User.is_suspended.is_(False),
        User.is_deleted.is_(False),
    )
    hidden = blocked_ids(viewer)
    if hidden:
        q = q.filter(Trip.user_id.notin_(hidden))
    if viewer.gender != 'female':
        q = q.filter(Trip.female_only_preference.is_(False))
    return q


def can_see_trip(viewer, trip):
    return visible_trips_query(viewer).filter(Trip.id == trip.id).first() is not None


_STOPWORDS = {'railway', 'station', 'bus', 'stand', 'isbt', 'airport', 'junction', 'jn', 'terminal',
              'city', 'campus', 'the', 'stop', 'metro', 'cantt', 'gate', 'main', 'new', 'old', 'sector'}


def place_tokens(place):
    words = re.findall(r'[a-z]+', place.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 1}


def places_match(a, b):
    """'Delhi', 'New Delhi Railway Station' and 'ISBT Delhi' all count as the same place."""
    ta, tb = place_tokens(a), place_tokens(b)
    if not ta or not tb:
        return a.strip().lower() == b.strip().lower()
    return bool(ta & tb)


def find_matches(my_trip, viewer, window_hours=TIME_WINDOW_HOURS):
    """Visible trips with the same destination departing within `window_hours` of my trip."""
    lo = (my_trip.departs_at - timedelta(hours=window_hours)).date().isoformat()
    hi = (my_trip.departs_at + timedelta(hours=window_hours)).date().isoformat()
    candidates = visible_trips_query(viewer).filter(Trip.date >= lo, Trip.date <= hi).all()

    matches = []
    for t in candidates:
        if not places_match(t.destination, my_trip.destination):
            continue
        if my_trip.female_only_preference and t.user.gender != 'female':
            continue
        gap = abs((t.departs_at - my_trip.departs_at).total_seconds()) / 60
        if gap > window_hours * 60:
            continue
        same_origin = places_match(t.origin, my_trip.origin)
        matches.append({'trip': t, 'gap_minutes': int(gap), 'same_origin': same_origin,
                        'same_mode': t.transport_mode == my_trip.transport_mode,
                        'same_batch': t.user.batch == viewer.batch})
    matches.sort(key=lambda m: (not m['same_origin'], m['gap_minutes'], not m['same_mode']))
    return matches


def existing_request(a, b, trip_ids):
    """An open (pending/accepted) request between users a and b touching any of the given trips."""
    return CompanionRequest.query.filter(
        CompanionRequest.status.in_(['pending', 'accepted']),
        or_(and_(CompanionRequest.sender_id == a.id, CompanionRequest.receiver_id == b.id),
            and_(CompanionRequest.sender_id == b.id, CompanionRequest.receiver_id == a.id)),
        or_(CompanionRequest.trip_id.in_(trip_ids), CompanionRequest.sender_trip_id.in_(trip_ids)),
    ).first()


@app.template_filter('nice_date')
def nice_date(value):
    try:
        return datetime.strptime(value, '%Y-%m-%d').strftime('%a, %d %b %Y')
    except (TypeError, ValueError):
        return value


@app.template_filter('local_time')
def local_time(dt):
    """UTC timestamp from the database shown in India time."""
    return dt.replace(tzinfo=timezone.utc).astimezone(APP_TZ).strftime('%d %b, %H:%M')


@app.template_filter('gap')
def fmt_gap(minutes):
    if minutes < 60:
        return f"{minutes} min apart"
    h, m = divmod(minutes, 60)
    return f"{h}h {m}m apart" if m else f"{h}h apart"


# ───────────────────────── Auth ─────────────────────────
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/register', methods=['GET', 'POST'])
@limiter.limit('10 per hour', methods=['POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        f = request.form
        name = f.get('name', '').strip()
        email = f.get('email', '').strip().lower()
        password = f.get('password', '')
        gender = f.get('gender', '')
        college = f.get('college', '')
        batch = f.get('batch', '').strip()
        if batch == 'custom':
            batch = f.get('custom_batch', '').strip()

        error = None
        if not name or not email or not password or not batch:
            error = 'Please fill in every required field.'
        elif not is_student_email(email):
            error = f'Use your college email address (ending in {EMAIL_HINT}).'
        elif len(password) < 8:
            error = 'Password must be at least 8 characters.'
        elif gender not in ('male', 'female', 'other'):
            error = 'Please select your gender.'
        elif college not in CAMPUSES:
            error = 'Please pick your college from the list.'
        elif User.query.filter_by(email=email).first():
            error = 'An account with this email already exists. Log in instead.'
        if error:
            flash(error)
            return render_template('register.html', colleges=CAMPUSES, form=f)

        session['pending_signup'] = {
            'name': name, 'email': email, 'password_hash': generate_password_hash(password),
            'gender': gender, 'college': college, 'batch': batch,
            'contact_info': f.get('contact_info', '').strip()[:100],
        }
        return start_verification(email)

    return render_template('register.html', colleges=CAMPUSES, form={})


def start_verification(email):
    result = send_otp(email)
    if result is None:
        flash('We could not send the verification email. Please try again later.')
        return redirect(url_for('register'))
    if result:
        session['dev_otp'] = result
    else:
        session.pop('dev_otp', None)
        flash(f'We sent a 6-digit code to {email}.')
    return redirect(url_for('verify_otp'))


@app.route('/verify-otp', methods=['GET', 'POST'])
@limiter.limit('20 per hour', methods=['POST'])
def verify_otp():
    pending = session.get('pending_signup')
    if not pending:
        flash('No sign-up in progress. Please start again.')
        return redirect(url_for('register'))

    if request.method == 'POST':
        error = check_otp(pending['email'], request.form.get('otp', '').strip())
        if error:
            flash(error)
            return render_template('verify_otp.html', email=pending['email'], dev_otp=session.get('dev_otp'))

        user = User.query.filter_by(email=pending['email']).first()
        if not user:
            user = User(name=pending['name'], email=pending['email'],
                        password_hash=pending.get('password_hash'), google_id=pending.get('google_id'),
                        gender=pending.get('gender', ''), college=pending.get('college', ''),
                        batch=pending.get('batch', ''), contact_info=pending.get('contact_info', ''))
            db.session.add(user)
        user.is_verified = True
        db.session.commit()
        session.pop('pending_signup', None)
        session.pop('dev_otp', None)
        login_user(user)
        flash(f'Your college email is verified. Welcome, {user.name.split()[0]}.')
        return redirect(url_for('dashboard') if user.profile_complete else url_for('setup_profile'))

    return render_template('verify_otp.html', email=pending['email'], dev_otp=session.get('dev_otp'))


@app.route('/resend-otp', methods=['POST'])
@limiter.limit('5 per hour')
def resend_otp():
    pending = session.get('pending_signup')
    if not pending:
        return redirect(url_for('register'))
    return start_verification(pending['email'])


@app.route('/login', methods=['GET', 'POST'])
@limiter.limit('10 per minute; 50 per hour', methods=['POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        user = User.query.filter_by(email=email).first()
        if not user or user.is_deleted or not user.password_hash or not check_password_hash(user.password_hash, request.form.get('password', '')):
            flash('Wrong email or password.')
        elif user.is_suspended:
            flash('This account is suspended while reports are reviewed.')
        else:
            login_user(user, remember=True)
            nxt = request.args.get('next', '')
            return redirect(nxt if nxt.startswith('/') and not nxt.startswith('//') else url_for('dashboard'))
    return render_template('login.html')


@app.route('/forgot-password', methods=['GET', 'POST'])
@limiter.limit('5 per hour', methods=['POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        user = User.query.filter_by(email=email, is_deleted=False).first()
        session['reset_email'] = email
        session.pop('dev_otp', None)
        if user:
            result = send_otp(email, purpose='reset')
            if result:
                session['dev_otp'] = result
        # Same message whether or not the account exists, so nobody can probe for accounts.
        flash(f'If {email} has an account, we sent it a 6-digit code.')
        return redirect(url_for('reset_password'))
    return render_template('forgot_password.html')


@app.route('/reset-password', methods=['GET', 'POST'])
@limiter.limit('20 per hour', methods=['POST'])
def reset_password():
    email = session.get('reset_email')
    if not email:
        return redirect(url_for('forgot_password'))
    if request.method == 'POST':
        password = request.form.get('password', '')
        if len(password) < 8:
            flash('Password must be at least 8 characters.')
        else:
            error = check_otp(email, request.form.get('otp', '').strip())
            user = User.query.filter_by(email=email, is_deleted=False).first()
            if error or not user:
                flash(error or 'Wrong code. Try again.')
            else:
                user.password_hash = generate_password_hash(password)
                db.session.commit()
                session.pop('reset_email', None)
                session.pop('dev_otp', None)
                flash('Password changed. Log in with your new password.')
                return redirect(url_for('login'))
    return render_template('reset_password.html', email=email, dev_otp=session.get('dev_otp'))


@app.route('/login/google')
def login_google():
    if google is None:
        flash('Google sign-in is not configured. Use email and password.')
        return redirect(url_for('login'))
    return google.authorize_redirect(url_for('login_google_callback', _external=True))


@app.route('/login/google/callback')
def login_google_callback():
    if google is None:
        return redirect(url_for('login'))
    try:
        token = google.authorize_access_token()
        info = token.get('userinfo') or google.userinfo()
    except Exception:  # noqa: BLE001
        flash('Google authentication failed. Please try again.')
        return redirect(url_for('login'))

    email = info.get('email', '').lower()
    if not is_student_email(email):
        flash(f'Only college email addresses ending in {EMAIL_HINT} are allowed.')
        return redirect(url_for('login'))

    user = User.query.filter_by(email=email).first()
    if user and user.is_verified:
        login_user(user, remember=True)
        return redirect(url_for('dashboard') if user.profile_complete else url_for('setup_profile'))

    session['pending_signup'] = {'name': info.get('name') or email.split('@')[0], 'email': email,
                                 'google_id': info.get('sub')}
    return start_verification(email)


@app.route('/setup-profile', methods=['GET', 'POST'])
@login_required
def setup_profile():
    user = current_user
    if user.profile_complete:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        gender = request.form.get('gender', '')
        college = request.form.get('college', '')
        batch = request.form.get('batch', '').strip()
        if batch == 'custom':
            batch = request.form.get('custom_batch', '').strip()
        if gender not in ('male', 'female', 'other') or college not in CAMPUSES or not batch:
            flash('Please choose your gender, college and batch.')
        else:
            user.gender, user.college, user.batch = gender, college, batch
            user.contact_info = request.form.get('contact_info', '').strip()[:100]
            db.session.commit()
            flash('Profile saved.')
            return redirect(url_for('dashboard'))
    return render_template('setup_profile.html', colleges=CAMPUSES)


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))


@app.route('/profile', methods=['GET', 'POST'])
@profile_required
def profile():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if name:
            current_user.name = name[:100]
        current_user.contact_info = request.form.get('contact_info', '').strip()[:100]
        current_user.notify_by_email = 'notify_by_email' in request.form
        db.session.commit()
        flash('Profile updated.')
        return redirect(url_for('profile'))
    blocks = Block.query.filter_by(blocker_id=current_user.id).all()
    return render_template('profile.html', blocks=blocks)


@app.route('/account/delete', methods=['POST'])
@login_required
def delete_account():
    user = current_user
    if not user.password_hash or not check_password_hash(user.password_hash, request.form.get('password', '')):
        flash('That password is not right, so your account was not deleted.')
        return redirect(url_for('profile'))
    for t in user.trips:
        t.is_cancelled = True
    for r in CompanionRequest.query.filter(or_(CompanionRequest.sender_id == user.id, CompanionRequest.receiver_id == user.id),
                                           CompanionRequest.status.in_(['pending', 'accepted'])):
        r.status = 'cancelled'
    ChatMessage.query.filter_by(sender_id=user.id).update({'message': '[deleted]'})
    Block.query.filter_by(blocker_id=user.id).delete()
    OTP.query.filter_by(email=user.email).delete()
    # Keep the row so reports and old requests still make sense, but remove everything personal.
    user.name, user.contact_info, user.password_hash, user.google_id = 'Deleted user', '', None, None
    user.email = f'deleted-{user.id}-{secrets.token_hex(4)}@deleted.invalid'
    user.is_deleted, user.notify_by_email = True, False
    db.session.commit()
    logout_user()
    flash('Your account and personal details have been deleted.')
    return redirect(url_for('index'))


@app.route('/privacy')
def privacy():
    return render_template('privacy.html')


# ───────────────────────── Trips ─────────────────────────
@app.route('/dashboard')
@profile_required
def dashboard():
    my_trips = [t for t in Trip.query.filter_by(user_id=current_user.id).order_by(Trip.date, Trip.time).all() if t.is_upcoming]
    match_counts = {t.id: len(find_matches(t, current_user)) for t in my_trips}
    hidden = blocked_ids(current_user)

    received = [r for r in CompanionRequest.query.filter_by(receiver_id=current_user.id, status='pending')
                .order_by(CompanionRequest.created_at.desc()).all() if r.sender_id not in hidden]
    sent = CompanionRequest.query.filter_by(sender_id=current_user.id).filter(
        CompanionRequest.status.in_(['pending', 'declined'])).order_by(CompanionRequest.created_at.desc()).all()
    companions = [r for r in CompanionRequest.query.filter(
        CompanionRequest.status == 'accepted',
        or_(CompanionRequest.sender_id == current_user.id, CompanionRequest.receiver_id == current_user.id)
    ).order_by(CompanionRequest.created_at.desc()).all() if r.trip.is_upcoming and r.other(current_user).id not in hidden]

    campus_trips = visible_trips_query(current_user).order_by(Trip.date, Trip.time).limit(8).all()

    return render_template('dashboard.html', trips=my_trips, match_counts=match_counts,
                           received=received, sent=sent, companions=companions, campus_trips=campus_trips)


@app.route('/trips/new', methods=['GET', 'POST'])
@profile_required
def create_trip():
    if request.method == 'POST':
        f = request.form
        origin, destination = f.get('origin', '').strip(), f.get('destination', '').strip()
        trip_date, trip_time, mode = f.get('date', ''), f.get('time', ''), f.get('transport_mode', '')
        error = None
        try:
            departs = datetime.strptime(f"{trip_date} {trip_time}", '%Y-%m-%d %H:%M')
        except ValueError:
            departs = None
        if not origin or not destination:
            error = 'Please enter where you are starting from and where you are going.'
        elif departs is None:
            error = 'Please enter a valid date and time.'
        elif departs.date() < local_today():
            error = 'The travel date cannot be in the past.'
        elif mode not in TRANSPORT_MODES:
            error = 'Please pick a mode of transport.'
        if error:
            flash(error)
            return render_template('create_trip.html', today=local_today().isoformat(), form=f)

        trip = Trip(user_id=current_user.id, origin=origin[:100], destination=destination[:100],
                    date=trip_date, time=trip_time, transport_mode=mode,
                    preferences=f.get('preferences', '').strip()[:300],
                    female_only_preference=('female_only' in f and current_user.gender == 'female'))
        db.session.add(trip)
        db.session.commit()
        n = len(find_matches(trip, current_user))
        flash(f'Trip posted! We found {n} possible companion{"s" if n != 1 else ""}.' if n
              else 'Trip posted! No matches yet. We will show them here as classmates post their trips.')
        return redirect(url_for('trip_matches', trip_id=trip.id))
    return render_template('create_trip.html', today=local_today().isoformat(), form={})


@app.route('/trips/<int:trip_id>/cancel', methods=['POST'])
@profile_required
def cancel_trip(trip_id):
    trip = db.get_or_404(Trip, trip_id)
    if trip.user_id != current_user.id:
        abort(403)
    trip.is_cancelled = True
    for r in CompanionRequest.query.filter(or_(CompanionRequest.trip_id == trip.id, CompanionRequest.sender_trip_id == trip.id),
                                           CompanionRequest.status.in_(['pending', 'accepted'])):
        r.status = 'cancelled'
    db.session.commit()
    flash('Trip cancelled. Any companions on it can see it is off.')
    return redirect(url_for('dashboard'))


@app.route('/trips/<int:trip_id>/matches')
@profile_required
def trip_matches(trip_id):
    trip = db.get_or_404(Trip, trip_id)
    if trip.user_id != current_user.id:
        abort(403)
    try:
        window = max(1, min(12, int(request.args.get('window', TIME_WINDOW_HOURS))))
    except ValueError:
        window = TIME_WINDOW_HOURS
    matches = find_matches(trip, current_user, window)
    if request.args.get('batch') == '1':
        matches = [m for m in matches if m['same_batch']]
    for m in matches:
        m['request'] = existing_request(current_user, m['trip'].user, [trip.id, m['trip'].id])
    return render_template('matches.html', trip=trip, matches=matches, window=window,
                           batch_only=request.args.get('batch') == '1')


@app.route('/search')
@profile_required
def search():
    """Browse campus trips by destination/date, even without posting a trip first."""
    dest = request.args.get('destination', '').strip()
    on = request.args.get('date', '').strip()
    q = visible_trips_query(current_user)
    if on:
        q = q.filter(Trip.date == on)
    trips = q.order_by(Trip.date, Trip.time).all()
    if dest:
        trips = [t for t in trips if places_match(t.destination, dest)]
    if request.args.get('women') == '1':
        trips = [t for t in trips if t.user.gender == 'female']
    my_trips = [t for t in current_user.trips if t.is_upcoming]
    return render_template('search.html', trips=trips, destination=dest, date=on,
                           women_only=request.args.get('women') == '1', has_trips=bool(my_trips))


# ───────────────────────── Companion requests ─────────────────────────
@app.route('/trips/<int:trip_id>/request', methods=['GET', 'POST'])
@profile_required
def send_request(trip_id):
    trip = db.get_or_404(Trip, trip_id)
    if trip.user_id == current_user.id:
        flash('That is your own trip.')
        return redirect(url_for('dashboard'))
    if not can_see_trip(current_user, trip):
        abort(404)

    my_trips = [t for t in current_user.trips if t.is_upcoming and places_match(t.destination, trip.destination)]
    if trip.female_only_preference and current_user.gender != 'female':
        abort(404)
    if existing_request(current_user, trip.user, [trip.id] + [t.id for t in my_trips]):
        flash(f'You already have a request with {trip.user.public_name} for this journey. Check your dashboard.')
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        sender_trip_id = request.form.get('sender_trip_id', type=int)
        sender_trip = next((t for t in my_trips if t.id == sender_trip_id), None)
        req = CompanionRequest(sender_id=current_user.id, receiver_id=trip.user_id, trip_id=trip.id,
                               sender_trip_id=sender_trip.id if sender_trip else None,
                               note=request.form.get('note', '').strip()[:300],
                               shared_contact='share_contact' in request.form and bool(current_user.contact_info))
        db.session.add(req)
        db.session.commit()
        notify(trip.user, f'{current_user.public_name} wants to travel with you',
               f'{current_user.public_name} (batch {current_user.batch}) sent a request for your trip '
               f'{trip.origin} to {trip.destination} on {nice_date(trip.date)}. Open your dashboard to accept or decline.')
        flash(f'Request sent to {trip.user.public_name} If they accept, chat opens on your dashboard.')
        return redirect(url_for('dashboard'))

    return render_template('send_request.html', trip=trip, my_trips=my_trips)


def _own_request(request_id, role):
    req = db.get_or_404(CompanionRequest, request_id)
    if getattr(req, f'{role}_id') != current_user.id:
        abort(403)
    return req


@app.route('/requests/<int:request_id>/accept', methods=['POST'])
@profile_required
def accept_request(request_id):
    req = _own_request(request_id, 'receiver')
    if req.status != 'pending' or is_blocked_between(req.sender, req.receiver):
        flash('This request is no longer open.')
        return redirect(url_for('dashboard'))
    req.status = 'accepted'
    req.receiver_shared_contact = 'share_contact' in request.form and bool(current_user.contact_info)
    db.session.commit()
    notify(req.sender, f'{current_user.name} accepted your request',
           f'{current_user.name} accepted your request for {req.trip.origin} to {req.trip.destination} '
           f'on {nice_date(req.trip.date)}. You can now chat on Travel Buddy and agree where to meet.')
    flash(f'You and {req.sender.name} are travelling together. Use the chat to agree where to meet.')
    return redirect(url_for('messages', request_id=req.id))


@app.route('/requests/<int:request_id>/decline', methods=['POST'])
@profile_required
def decline_request(request_id):
    req = _own_request(request_id, 'receiver')
    if req.status == 'pending':
        req.status = 'declined'
        db.session.commit()
        flash('Request declined. They will only see that it was not accepted.')
    return redirect(url_for('dashboard'))


@app.route('/requests/<int:request_id>/cancel', methods=['POST'])
@profile_required
def cancel_request(request_id):
    req = db.get_or_404(CompanionRequest, request_id)
    if current_user.id not in (req.sender_id, req.receiver_id):
        abort(403)
    if req.status in ('pending', 'accepted'):
        was_accepted = req.status == 'accepted'
        req.status = 'cancelled'
        db.session.commit()
        flash('Plan cancelled. Your companion can see it is off.' if was_accepted else 'Request withdrawn.')
    return redirect(url_for('dashboard'))


@app.route('/requests/<int:request_id>/chat', methods=['GET', 'POST'])
@profile_required
def messages(request_id):
    req = db.get_or_404(CompanionRequest, request_id)
    if current_user.id not in (req.sender_id, req.receiver_id) or req.status != 'accepted':
        flash('Chat opens once a companion request is accepted.')
        return redirect(url_for('dashboard'))
    other = req.other(current_user)
    if is_blocked_between(current_user, other):
        flash('This conversation is closed.')
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        text = request.form.get('message', '').strip()
        if text:
            db.session.add(ChatMessage(sender_id=current_user.id, receiver_id=other.id,
                                       companion_request_id=req.id, message=text[:2000]))
            db.session.commit()
        return redirect(url_for('messages', request_id=req.id))

    chat = ChatMessage.query.filter_by(companion_request_id=req.id).order_by(ChatMessage.sent_at).all()
    return render_template('messages.html', req=req, other_user=other, trip=req.trip, messages=chat,
                           i_shared=req.contact_shared_by(current_user), they_shared=req.contact_shared_by(other))


@app.route('/requests/<int:request_id>/messages.json')
@profile_required
def messages_json(request_id):
    req = db.get_or_404(CompanionRequest, request_id)
    if current_user.id not in (req.sender_id, req.receiver_id) or req.status != 'accepted':
        abort(403)
    after = request.args.get('after', 0, type=int)
    rows = ChatMessage.query.filter(ChatMessage.companion_request_id == req.id, ChatMessage.id > after)\
        .order_by(ChatMessage.id).limit(100).all()
    return {'messages': [{'id': m.id, 'mine': m.sender_id == current_user.id, 'text': m.message,
                          'time': local_time(m.sent_at)} for m in rows]}


@app.route('/requests/<int:request_id>/share-contact', methods=['POST'])
@profile_required
def share_contact(request_id):
    req = db.get_or_404(CompanionRequest, request_id)
    if current_user.id not in (req.sender_id, req.receiver_id) or req.status != 'accepted':
        abort(403)
    share = request.form.get('share') == '1'
    if share and not current_user.contact_info:
        flash('Add your WhatsApp/Instagram on your profile first.')
        return redirect(url_for('profile'))
    if current_user.id == req.sender_id:
        req.shared_contact = share
    else:
        req.receiver_shared_contact = share
    db.session.commit()
    flash('Your contact is now visible to your companion.' if share else 'Your contact is hidden again.')
    return redirect(url_for('messages', request_id=req.id))


# ───────────────────────── Safety: block & report ─────────────────────────
def _close_requests_between(a, b):
    for r in CompanionRequest.query.filter(
            CompanionRequest.status.in_(['pending', 'accepted']),
            or_(and_(CompanionRequest.sender_id == a.id, CompanionRequest.receiver_id == b.id),
                and_(CompanionRequest.sender_id == b.id, CompanionRequest.receiver_id == a.id))):
        r.status = 'cancelled'


@app.route('/users/<int:user_id>/block', methods=['POST'])
@profile_required
def block_user(user_id):
    other = db.get_or_404(User, user_id)
    if other.id == current_user.id:
        abort(400)
    if not Block.query.filter_by(blocker_id=current_user.id, blocked_id=other.id).first():
        db.session.add(Block(blocker_id=current_user.id, blocked_id=other.id))
    _close_requests_between(current_user, other)
    db.session.commit()
    flash(f'{other.public_name} is blocked. You will not see each other\'s trips or requests.')
    return redirect(url_for('dashboard'))


@app.route('/users/<int:user_id>/unblock', methods=['POST'])
@profile_required
def unblock_user(user_id):
    Block.query.filter_by(blocker_id=current_user.id, blocked_id=user_id).delete()
    db.session.commit()
    flash('Unblocked.')
    return redirect(url_for('profile'))


@app.route('/users/<int:user_id>/report', methods=['GET', 'POST'])
@profile_required
def report_user(user_id):
    other = db.get_or_404(User, user_id)
    if other.id == current_user.id:
        abort(400)
    if request.method == 'POST':
        reason = request.form.get('reason', '')
        if reason not in REPORT_REASONS:
            flash('Please choose a reason.')
            return render_template('report.html', other=other, reasons=REPORT_REASONS)
        db.session.add(Report(reporter_id=current_user.id, reported_id=other.id, reason=reason,
                              details=request.form.get('details', '').strip()[:2000]))
        if 'also_block' in request.form and not Block.query.filter_by(blocker_id=current_user.id, blocked_id=other.id).first():
            db.session.add(Block(blocker_id=current_user.id, blocked_id=other.id))
            _close_requests_between(current_user, other)
        db.session.flush()
        reporters = db.session.query(Report.reporter_id).filter_by(reported_id=other.id, status='open').distinct().count()
        if reporters >= AUTO_HIDE_REPORT_THRESHOLD:
            other.is_suspended = True
        db.session.commit()
        flash('Thanks for reporting. Our team will review it. Reports are confidential.')
        return redirect(url_for('dashboard'))
    return render_template('report.html', other=other, reasons=REPORT_REASONS)


@app.route('/admin/reports')
@admin_required
def admin_reports():
    reports = Report.query.order_by(Report.status.desc(), Report.created_at.desc()).all()
    return render_template('admin_reports.html', reports=reports)


@app.route('/admin/reports/<int:report_id>/<action>', methods=['POST'])
@admin_required
def admin_report_action(report_id, action):
    report = db.get_or_404(Report, report_id)
    if action == 'suspend':
        report.reported.is_suspended = True
        report.status = 'actioned'
    elif action == 'reinstate':
        report.reported.is_suspended = False
        report.status = 'dismissed'
    elif action == 'dismiss':
        report.status = 'dismissed'
    else:
        abort(400)
    db.session.commit()
    return redirect(url_for('admin_reports'))


# ───────────────────────── Installable app (PWA) & errors ─────────────────────────
@app.route('/sw.js')
def service_worker():
    # Served from the site root so it can control every page.
    resp = app.send_static_file('sw.js')
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['Service-Worker-Allowed'] = '/'
    return resp


@app.route('/manifest.webmanifest')
def manifest():
    resp = app.send_static_file('manifest.webmanifest')
    resp.headers['Content-Type'] = 'application/manifest+json'
    return resp


@app.route('/offline')
def offline():
    return render_template('offline.html')


@app.errorhandler(429)
def too_many_requests(e):
    return render_template('error.html', title='Slow down',
                           message='Too many tries in a short time. Wait a few minutes and try again.'), 429


@app.errorhandler(404)
def not_found(e):
    return render_template('error.html', title='Not found',
                           message="That page doesn't exist, or you don't have access to it."), 404


@app.route('/healthz')
def healthz():
    return 'ok'


# ───────────────────────── Demo data ─────────────────────────

@app.cli.command('seed')
def seed():
    """Add demo students and trips (password for all: password123)."""
    college = CAMPUSES[0]
    tomorrow = (local_today() + timedelta(days=1)).isoformat()
    people = [
        ('Priya Sharma', 'priya@thapar.edu', 'female', '2025', '@priya.travels',
         ('Patiala', 'Delhi', '18:00', 'bus', True, 'Prefer window seat, quiet journey')),
        ('Ananya Gupta', 'ananya@thapar.edu', 'female', '2026', '+91 98xxxxxx01',
         ('Patiala Bus Stand', 'New Delhi', '19:30', 'bus', False, '')),
        ('Rahul Verma', 'rahul@thapar.edu', 'male', '2025', '@rahul.v',
         ('Patiala', 'Delhi ISBT', '17:15', 'bus', False, 'Happy to split a cab to the bus stand')),
        ('Karan Mehta', 'karan@thapar.edu', 'male', '2024', '',
         ('Patiala', 'Chandigarh', '09:00', 'car', False, '')),
    ]
    for name, email, gender, batch, contact, (o, d, t, mode, fem, prefs) in people:
        if User.query.filter_by(email=email).first():
            continue
        u = User(name=name, email=email, password_hash=generate_password_hash('password123'), gender=gender,
                 college=college, batch=batch, contact_info=contact, is_verified=True)
        db.session.add(u)
        db.session.flush()
        db.session.add(Trip(user_id=u.id, origin=o, destination=d, date=tomorrow, time=t,
                            transport_mode=mode, female_only_preference=fem, preferences=prefs))
    db.session.commit()
    click.echo(f'Demo data ready at "{college}". Log in as priya@thapar.edu / password123 (or ananya, rahul, karan).')


if __name__ == '__main__':
    from flask_migrate import upgrade
    with app.app_context():
        upgrade()
    app.run(debug=True)
