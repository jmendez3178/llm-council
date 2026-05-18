from flask import Flask, request, jsonify, render_template, session, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import anthropic, json, math, os, re, secrets
from datetime import datetime, date
import calendar

# Load .env file if present (local development)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'), override=True)
except ImportError:
    pass

app = Flask(__name__)

# ── Config ─────────────────────────────────────────────────────────────────
db_dir = os.environ.get('DATA_DIR', os.path.dirname(os.path.abspath(__file__)))
db_path = os.path.join(db_dir, 'booksai.db')
app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{db_path}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
db = SQLAlchemy(app)


# ── Models ─────────────────────────────────────────────────────────────────

class User(db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    email         = db.Column(db.String(120), unique=True, nullable=False)
    name          = db.Column(db.String(100), default='')
    password_hash = db.Column(db.String(200), nullable=False)
    is_admin      = db.Column(db.Boolean, default=False)
    plan          = db.Column(db.String(20), default='active')   # active | suspended
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    stripe_customer_id = db.Column(db.String(200), default='')
    stripe_sub_id      = db.Column(db.String(200), default='')

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw, method='pbkdf2:sha256')

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)

    def to_dict(self):
        tx_count = Transaction.query.filter_by(user_id=self.id).count()
        return {'id': self.id, 'email': self.email, 'name': self.name,
                'is_admin': self.is_admin, 'plan': self.plan,
                'created_at': self.created_at.strftime('%Y-%m-%d'),
                'tx_count': tx_count}


class Transaction(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    date        = db.Column(db.String(20), nullable=False)
    description = db.Column(db.String(300))
    amount      = db.Column(db.Float, nullable=False, default=0)
    category    = db.Column(db.String(100), default='Uncategorized')
    tx_type     = db.Column(db.String(20), default='expense')
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {'id': self.id, 'date': self.date, 'description': self.description,
                'amount': self.amount, 'category': self.category, 'tx_type': self.tx_type}


class Invoice(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    user_id      = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    number       = db.Column(db.String(50))
    client_name  = db.Column(db.String(200))
    client_email = db.Column(db.String(200))
    date         = db.Column(db.String(20))
    due_date     = db.Column(db.String(20))
    status       = db.Column(db.String(20), default='draft')
    subtotal     = db.Column(db.Float, default=0)
    tax_rate     = db.Column(db.Float, default=0)
    total        = db.Column(db.Float, default=0)
    notes        = db.Column(db.Text)
    items        = db.relationship('InvoiceItem', backref='invoice', lazy=True,
                                   cascade='all,delete-orphan')
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {'id': self.id, 'number': self.number, 'client_name': self.client_name,
                'client_email': self.client_email, 'date': self.date, 'due_date': self.due_date,
                'status': self.status, 'subtotal': self.subtotal, 'tax_rate': self.tax_rate,
                'total': self.total, 'notes': self.notes,
                'items': [it.to_dict() for it in self.items]}


class InvoiceItem(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    invoice_id  = db.Column(db.Integer, db.ForeignKey('invoice.id'), nullable=False)
    description = db.Column(db.String(300))
    quantity    = db.Column(db.Float, default=1)
    rate        = db.Column(db.Float, default=0)
    amount      = db.Column(db.Float, default=0)

    def to_dict(self):
        return {'id': self.id, 'description': self.description,
                'quantity': self.quantity, 'rate': self.rate, 'amount': self.amount}


class Expense(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    date        = db.Column(db.String(20))
    vendor      = db.Column(db.String(200))
    description = db.Column(db.String(300))
    amount      = db.Column(db.Float, default=0)
    category    = db.Column(db.String(100), default='Uncategorized')
    status      = db.Column(db.String(20), default='pending')
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {'id': self.id, 'date': self.date, 'vendor': self.vendor,
                'description': self.description, 'amount': self.amount,
                'category': self.category, 'status': self.status}


class Employee(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    name       = db.Column(db.String(200))
    title      = db.Column(db.String(200))
    email      = db.Column(db.String(200))
    pay_type   = db.Column(db.String(20), default='salary')
    pay_rate   = db.Column(db.Float, default=0)
    start_date = db.Column(db.String(20))
    status     = db.Column(db.String(20), default='active')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {'id': self.id, 'name': self.name, 'title': self.title,
                'email': self.email, 'pay_type': self.pay_type, 'pay_rate': self.pay_rate,
                'start_date': self.start_date, 'status': self.status}


class PayrollRun(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    user_id      = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    period_start = db.Column(db.String(20))
    period_end   = db.Column(db.String(20))
    pay_date     = db.Column(db.String(20))
    status       = db.Column(db.String(20), default='draft')
    total_gross  = db.Column(db.Float, default=0)
    total_net    = db.Column(db.Float, default=0)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {'id': self.id, 'period_start': self.period_start,
                'period_end': self.period_end, 'pay_date': self.pay_date,
                'status': self.status, 'total_gross': self.total_gross,
                'total_net': self.total_net}


class PayrollRecord(db.Model):
    """Stores imported payroll data rows (e.g. from Kaggle / city payroll CSVs)."""
    id              = db.Column(db.Integer, primary_key=True)
    user_id         = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    pay_year        = db.Column(db.Integer, default=0)
    department_no   = db.Column(db.String(50), default='')
    department_name = db.Column(db.String(200), default='')
    job_class_no    = db.Column(db.String(50), default='')
    job_title       = db.Column(db.String(200), default='')
    employment_type = db.Column(db.String(50), default='')
    job_status      = db.Column(db.String(50), default='')
    mou             = db.Column(db.String(50), default='')
    regular_pay     = db.Column(db.Float, default=0)
    overtime_pay    = db.Column(db.Float, default=0)
    other_pay       = db.Column(db.Float, default=0)
    total_pay       = db.Column(db.Float, default=0)
    benefits        = db.Column(db.Float, default=0)
    total_pay_benefits = db.Column(db.Float, default=0)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id, 'pay_year': self.pay_year,
            'department_no': self.department_no, 'department_name': self.department_name,
            'job_class_no': self.job_class_no, 'job_title': self.job_title,
            'employment_type': self.employment_type, 'job_status': self.job_status,
            'mou': self.mou, 'regular_pay': self.regular_pay,
            'overtime_pay': self.overtime_pay, 'other_pay': self.other_pay,
            'total_pay': self.total_pay, 'benefits': self.benefits,
            'total_pay_benefits': self.total_pay_benefits,
        }


class PayStub(db.Model):
    """One uploaded pay stub (PDF or image) per row."""
    id                 = db.Column(db.Integer, primary_key=True)
    user_id            = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    filename           = db.Column(db.String(300), default='')
    employer_name      = db.Column(db.String(200), default='')
    employee_name      = db.Column(db.String(200), default='')
    employee_id        = db.Column(db.String(100), default='')
    department         = db.Column(db.String(200), default='')
    job_title          = db.Column(db.String(200), default='')
    pay_date           = db.Column(db.String(20),  default='')
    period_start       = db.Column(db.String(20),  default='')
    period_end         = db.Column(db.String(20),  default='')
    regular_pay        = db.Column(db.Float, default=0)
    overtime_pay       = db.Column(db.Float, default=0)
    other_pay          = db.Column(db.Float, default=0)
    gross_pay          = db.Column(db.Float, default=0)
    federal_tax        = db.Column(db.Float, default=0)
    state_tax          = db.Column(db.Float, default=0)
    ss_tax             = db.Column(db.Float, default=0)
    medicare_tax       = db.Column(db.Float, default=0)
    other_deductions   = db.Column(db.Float, default=0)
    total_deductions   = db.Column(db.Float, default=0)
    net_pay            = db.Column(db.Float, default=0)
    notes              = db.Column(db.Text, default='')   # JSON extras
    created_at         = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id, 'filename': self.filename,
            'employer_name': self.employer_name, 'employee_name': self.employee_name,
            'employee_id': self.employee_id, 'department': self.department,
            'job_title': self.job_title, 'pay_date': self.pay_date,
            'period_start': self.period_start, 'period_end': self.period_end,
            'regular_pay': self.regular_pay, 'overtime_pay': self.overtime_pay,
            'other_pay': self.other_pay, 'gross_pay': self.gross_pay,
            'federal_tax': self.federal_tax, 'state_tax': self.state_tax,
            'ss_tax': self.ss_tax, 'medicare_tax': self.medicare_tax,
            'other_deductions': self.other_deductions,
            'total_deductions': self.total_deductions, 'net_pay': self.net_pay,
            'notes': self.notes,
            'created_at': self.created_at.strftime('%Y-%m-%d'),
        }


class MarketingCampaign(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    name        = db.Column(db.String(200))
    channel     = db.Column(db.String(100))
    start_date  = db.Column(db.String(20))
    end_date    = db.Column(db.String(20))
    budget      = db.Column(db.Float, default=0)
    spent       = db.Column(db.Float, default=0)
    impressions = db.Column(db.Integer, default=0)
    clicks      = db.Column(db.Integer, default=0)
    leads       = db.Column(db.Integer, default=0)
    revenue     = db.Column(db.Float, default=0)
    status      = db.Column(db.String(20), default='active')
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        roi = round(((self.revenue - self.spent) / self.spent * 100), 1) if self.spent else 0
        cpc = round(self.spent / self.clicks, 2) if self.clicks else 0
        return {'id': self.id, 'name': self.name, 'channel': self.channel,
                'start_date': self.start_date, 'end_date': self.end_date,
                'budget': self.budget, 'spent': self.spent, 'impressions': self.impressions,
                'clicks': self.clicks, 'leads': self.leads, 'revenue': self.revenue,
                'roi': roi, 'cpc': cpc, 'status': self.status}


class GoogleAdsConnection(db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    user_id       = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    developer_token = db.Column(db.String(200), default='')
    client_id     = db.Column(db.String(200), default='')
    client_secret = db.Column(db.String(200), default='')
    refresh_token = db.Column(db.String(500), default='')
    customer_id   = db.Column(db.String(100), default='')
    login_email   = db.Column(db.String(200), default='')
    connected_at  = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {'id': self.id, 'customer_id': self.customer_id,
                'login_email': self.login_email,
                'connected_at': self.connected_at.strftime('%Y-%m-%d')}


class GoogleAdsCampaign(db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    user_id       = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    name          = db.Column(db.String(200), default='')
    objective     = db.Column(db.String(100), default='')
    demographic   = db.Column(db.String(200), default='')
    budget_daily  = db.Column(db.Float, default=0)
    start_date    = db.Column(db.String(20), default='')
    end_date      = db.Column(db.String(20), default='')
    status        = db.Column(db.String(20), default='DRAFT')
    headline1     = db.Column(db.String(30), default='')
    headline2     = db.Column(db.String(30), default='')
    headline3     = db.Column(db.String(30), default='')
    description1  = db.Column(db.String(90), default='')
    description2  = db.Column(db.String(90), default='')
    final_url     = db.Column(db.String(500), default='')
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id, 'name': self.name, 'objective': self.objective,
            'demographic': self.demographic, 'budget_daily': self.budget_daily,
            'start_date': self.start_date, 'end_date': self.end_date,
            'status': self.status, 'headline1': self.headline1,
            'headline2': self.headline2, 'headline3': self.headline3,
            'description1': self.description1, 'description2': self.description2,
            'final_url': self.final_url,
            'created_at': self.created_at.strftime('%Y-%m-%d'),
        }


# ── Constants ──────────────────────────────────────────────────────────────

CATEGORIES = [
    'Revenue / Sales', 'Cost of Goods Sold', 'Payroll / Wages', 'Rent / Lease',
    'Utilities', 'Marketing / Advertising', 'Software / Subscriptions',
    'Professional Services', 'Travel / Transportation', 'Meals / Entertainment',
    'Office Supplies', 'Bank Fees / Finance Charges', 'Taxes / Licenses',
    'Insurance', 'Loan / Debt Payment', 'Owner Draw / Distribution',
    'Transfer', 'Uncategorized',
]

INCOME_CATEGORIES = {'Revenue / Sales', 'Transfer'}


# ── Auth helpers ───────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Unauthorized'}), 401
        user = User.query.get(session['user_id'])
        if not user:
            session.clear()
            return jsonify({'error': 'Unauthorized'}), 401
        if user.plan == 'suspended':
            session.clear()
            return jsonify({'error': 'Account suspended. Please contact support.'}), 403
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login_page'))
        user = User.query.get(session['user_id'])
        if not user or not user.is_admin:
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated

def current_uid():
    return session.get('user_id')


# ── Anthropic client ───────────────────────────────────────────────────────

def get_client():
    key = os.environ.get('ANTHROPIC_API_KEY', '').strip()
    if not key:
        raise ValueError('ANTHROPIC_API_KEY not set.')
    return anthropic.Anthropic(api_key=key)


# ── Excel Intelligence Agent ───────────────────────────────────────────────

EXCEL_TOOLS = [
    {
        "name": "inspect_column",
        "description": "Get the first 8 non-empty values from a specific column.",
        "input_schema": {
            "type": "object",
            "properties": {"column_name": {"type": "string"}},
            "required": ["column_name"]
        }
    },
    {
        "name": "set_column_mapping",
        "description": "Declare the final mapping once confident.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_column":        {"type": "string"},
                "description_column": {"type": "string"},
                "amount_column":      {"type": "string"},
                "data_type":          {"type": "string",
                                       "enum": ["bank_statement","sales_data","expenses","invoices","other"]},
                "issues": {"type": "array", "items": {"type": "string"}},
                "notes":  {"type": "string"}
            },
            "required": ["description_column", "amount_column", "data_type"]
        }
    }
]


def run_excel_agent(columns, sample_rows):
    col_list = ', '.join(f'"{c}"' for c in columns)
    system   = ("You are an expert at reading financial spreadsheets. "
                "Use inspect_column on ambiguous columns before deciding. "
                "Call set_column_mapping once confident.")
    user_msg = (f"Columns: {col_list}\n\nFirst 3 rows:\n{json.dumps(sample_rows[:3], indent=2)}\n\n"
                "Identify DATE, DESCRIPTION, and AMOUNT columns. "
                "Call set_column_mapping with your mapping, data type, issues, and a brief note.")
    messages  = [{"role": "user", "content": user_msg}]
    mapping, notes, issues, data_type = {}, "", [], "other"

    for _ in range(6):
        resp = get_client().messages.create(
            model="claude-sonnet-4-5", max_tokens=1024,
            system=system, tools=EXCEL_TOOLS, messages=messages)
        messages.append({"role": "assistant", "content": resp.content})

        tool_results, done = [], False
        for block in resp.content:
            if block.type != "tool_use":
                continue
            if block.name == "set_column_mapping":
                inp       = block.input or {}
                notes     = inp.pop("notes", "")
                issues    = inp.pop("issues", [])
                data_type = inp.pop("data_type", "other")
                mapping   = inp
                done      = True
                tool_results.append({"type": "tool_result", "tool_use_id": block.id,
                                     "content": "Mapping confirmed."})
            elif block.name == "inspect_column":
                col  = (block.input or {}).get("column_name", "")
                vals = [str(r.get(col, "")) for r in sample_rows
                        if str(r.get(col, "")).strip() not in ("", "nan", "None")][:8]
                tool_results.append({"type": "tool_result", "tool_use_id": block.id,
                                     "content": json.dumps(vals) if vals else '"(empty)"'})

        if tool_results:
            messages.append({"role": "user", "content": tool_results})
        if done or resp.stop_reason == "end_turn":
            break

    return mapping, notes, issues, data_type


# ── Auth routes ────────────────────────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login_page():
    if request.method == 'GET':
        if 'user_id' in session:
            return redirect(url_for('index'))
        return render_template('login.html')

    data  = request.json or {}
    email = data.get('email', '').strip().lower()
    pw    = data.get('password', '')
    user  = User.query.filter_by(email=email).first()

    if not user or not user.check_password(pw):
        return jsonify({'error': 'Invalid email or password'}), 401
    if user.plan == 'suspended':
        return jsonify({'error': 'Your account has been suspended. Please contact support.'}), 403

    session['user_id'] = user.id
    return jsonify({'ok': True, 'name': user.name, 'is_admin': user.is_admin})


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login_page'))


@app.route('/api/me')
def me():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    u = User.query.get(session['user_id'])
    if not u:
        session.clear()
        return jsonify({'error': 'Not logged in'}), 401
    return jsonify({'id': u.id, 'name': u.name, 'email': u.email, 'is_admin': u.is_admin})


# ── Admin routes ───────────────────────────────────────────────────────────

@app.route('/admin')
@admin_required
def admin_page():
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template('admin.html', users=[u.to_dict() for u in users])


@app.route('/admin/users', methods=['POST'])
@admin_required
def admin_create_user():
    d  = request.form
    email = d.get('email', '').strip().lower()
    name  = d.get('name', '').strip()
    pw    = d.get('password', '').strip()
    if not email or not pw:
        return redirect(url_for('admin_page'))
    if User.query.filter_by(email=email).first():
        return redirect(url_for('admin_page'))
    u = User(email=email, name=name, plan='active', is_admin=False)
    u.set_password(pw)
    db.session.add(u)
    db.session.commit()
    return redirect(url_for('admin_page'))


@app.route('/admin/users/<int:uid>/suspend', methods=['POST'])
@admin_required
def admin_suspend_user(uid):
    u = User.query.get_or_404(uid)
    u.plan = 'suspended'
    db.session.commit()
    return redirect(url_for('admin_page'))


@app.route('/admin/users/<int:uid>/activate', methods=['POST'])
@admin_required
def admin_activate_user(uid):
    u = User.query.get_or_404(uid)
    u.plan = 'active'
    db.session.commit()
    return redirect(url_for('admin_page'))


@app.route('/admin/users/<int:uid>/delete', methods=['POST'])
@admin_required
def admin_delete_user(uid):
    u = User.query.get_or_404(uid)
    # Remove user's data first
    for model in [InvoiceItem, Invoice, Transaction, Expense, Employee, PayrollRun, MarketingCampaign, PayrollRecord, PayStub, GoogleAdsConnection, GoogleAdsCampaign]:
        if hasattr(model, 'user_id'):
            model.query.filter_by(user_id=uid).delete()
    db.session.delete(u)
    db.session.commit()
    return redirect(url_for('admin_page'))


# ── Main route ─────────────────────────────────────────────────────────────

@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login_page'))
    return render_template('index.html')


@app.route('/landing')
def landing_page():
    return render_template('landing.html')


# ── Dashboard ──────────────────────────────────────────────────────────────

@app.route('/api/dashboard')
@login_required
def dashboard():
    uid  = current_uid()
    txns = Transaction.query.filter_by(user_id=uid).all()
    income   = sum(abs(t.amount) for t in txns if t.tx_type == 'income')
    expenses = sum(abs(t.amount) for t in txns if t.tx_type == 'expense')

    invoices        = Invoice.query.filter_by(user_id=uid).all()
    inv_outstanding = sum(i.total for i in invoices if i.status in ('draft', 'sent'))
    inv_paid        = sum(i.total for i in invoices if i.status == 'paid')

    today = date.today()
    monthly = []
    for i in range(5, -1, -1):
        yr  = today.year  + (today.month - 1 - i) // 12
        mo  = (today.month - 1 - i) % 12 + 1
        mo_date = date(yr, mo, 1)
        prefix  = mo_date.strftime('%Y-%m')
        mo_txns = [t for t in txns if t.date.startswith(prefix)]
        monthly.append({
            'label':    mo_date.strftime('%b %Y'),
            'income':   round(sum(abs(t.amount) for t in mo_txns if t.tx_type == 'income'), 2),
            'expenses': round(sum(abs(t.amount) for t in mo_txns if t.tx_type == 'expense'), 2),
        })

    by_cat = {}
    for t in txns:
        if t.tx_type == 'expense':
            by_cat[t.category] = by_cat.get(t.category, 0) + abs(t.amount)
    top_cats = sorted(by_cat.items(), key=lambda x: x[1], reverse=True)[:7]

    recent = sorted(txns, key=lambda t: t.date, reverse=True)[:10]

    return jsonify({
        'income':          round(income, 2),
        'expenses':        round(expenses, 2),
        'net':             round(income - expenses, 2),
        'inv_outstanding': round(inv_outstanding, 2),
        'inv_paid':        round(inv_paid, 2),
        'monthly':         monthly,
        'top_categories':  [{'name': k, 'amount': round(v, 2)} for k, v in top_cats],
        'recent':          [t.to_dict() for t in recent],
    })


# ── P&L Statement ──────────────────────────────────────────────────────────

@app.route('/api/pl')
@login_required
def pl_statement():
    uid  = current_uid()
    txns = Transaction.query.filter_by(user_id=uid).all()

    if txns:
        earliest = min(t.date[:7] for t in txns)
        today    = date.today()
        cur_yr, cur_mo = today.year, today.month
        ey, em = int(earliest[:4]), int(earliest[5:7])
        cols = []
        yr, mo_num = ey, em
        while (yr, mo_num) <= (cur_yr, cur_mo):
            mo = date(yr, mo_num, 1)
            cols.append({'label': mo.strftime('%b %Y'), 'prefix': mo.strftime('%Y-%m')})
            mo_num += 1
            if mo_num > 12:
                mo_num = 1
                yr += 1
    else:
        today = date.today()
        cols  = [{'label': today.strftime('%b %Y'), 'prefix': today.strftime('%Y-%m')}]

    def month_amounts(cat, tx_type):
        return [round(sum(abs(t.amount) for t in txns
                          if t.category == cat and t.tx_type == tx_type
                          and t.date.startswith(c['prefix'])), 2)
                for c in cols]

    income_cats  = sorted({t.category for t in txns if t.tx_type == 'income'})
    expense_cats = sorted({t.category for t in txns if t.tx_type == 'expense'})

    income_rows  = [{'category': c, 'months': month_amounts(c, 'income'),
                     'total': round(sum(month_amounts(c, 'income')), 2)}
                    for c in income_cats]
    expense_rows = [{'category': c, 'months': month_amounts(c, 'expense'),
                     'total': round(sum(month_amounts(c, 'expense')), 2)}
                    for c in expense_cats]

    total_income  = [round(sum(r['months'][i] for r in income_rows), 2)  for i in range(len(cols))]
    total_expense = [round(sum(r['months'][i] for r in expense_rows), 2) for i in range(len(cols))]
    net_profit    = [round(total_income[i] - total_expense[i], 2)        for i in range(len(cols))]

    return jsonify({
        'columns':       [c['label'] for c in cols],
        'income_rows':   income_rows,
        'expense_rows':  expense_rows,
        'total_income':  total_income,
        'total_expense': total_expense,
        'net_profit':    net_profit,
    })


# ── Transactions ───────────────────────────────────────────────────────────

@app.route('/api/transactions', methods=['GET'])
@login_required
def get_transactions():
    txns = Transaction.query.filter_by(user_id=current_uid()).order_by(Transaction.date.desc()).all()
    return jsonify([t.to_dict() for t in txns])


@app.route('/api/transactions', methods=['POST'])
@login_required
def create_transaction():
    d = request.json or {}
    t = Transaction(
        user_id=current_uid(),
        date=d.get('date', date.today().isoformat()),
        description=d.get('description', ''),
        amount=float(d.get('amount', 0)),
        category=d.get('category', 'Uncategorized'),
        tx_type=d.get('tx_type', 'expense'),
    )
    db.session.add(t)
    db.session.commit()
    return jsonify(t.to_dict()), 201


@app.route('/api/transactions/<int:tid>', methods=['PUT'])
@login_required
def update_transaction(tid):
    t = Transaction.query.filter_by(id=tid, user_id=current_uid()).first_or_404()
    d = request.json or {}
    for f in ('date', 'description', 'category', 'tx_type'):
        if f in d:
            setattr(t, f, d[f])
    if 'amount' in d:
        t.amount = float(d['amount'])
    db.session.commit()
    return jsonify(t.to_dict())


@app.route('/api/transactions/<int:tid>', methods=['DELETE'])
@login_required
def delete_transaction(tid):
    t = Transaction.query.filter_by(id=tid, user_id=current_uid()).first_or_404()
    db.session.delete(t)
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/transactions/bulk', methods=['POST'])
@login_required
def bulk_import_transactions():
    rows  = request.json or []
    uid   = current_uid()
    added = 0
    for r in rows:
        try:
            amt = float(str(r.get('amount', 0)).replace('$', '').replace(',', ''))
            t = Transaction(
                user_id=uid,
                date=r.get('date', date.today().isoformat()),
                description=r.get('description', ''),
                amount=abs(amt),
                category=r.get('category', 'Uncategorized'),
                tx_type=r.get('tx_type', 'income' if amt > 0 else 'expense'),
            )
            db.session.add(t)
            added += 1
        except Exception:
            continue
    db.session.commit()
    return jsonify({'added': added})


# ── Invoices ───────────────────────────────────────────────────────────────

@app.route('/api/invoices', methods=['GET'])
@login_required
def get_invoices():
    invs = Invoice.query.filter_by(user_id=current_uid()).order_by(Invoice.created_at.desc()).all()
    return jsonify([i.to_dict() for i in invs])


@app.route('/api/invoices', methods=['POST'])
@login_required
def create_invoice():
    d   = request.json or {}
    uid = current_uid()
    count = Invoice.query.filter_by(user_id=uid).count() + 1
    inv = Invoice(
        user_id=uid,
        number=d.get('number', f'INV-{count:03d}'),
        client_name=d.get('client_name', ''),
        client_email=d.get('client_email', ''),
        date=d.get('date', date.today().isoformat()),
        due_date=d.get('due_date', ''),
        status=d.get('status', 'draft'),
        tax_rate=float(d.get('tax_rate', 0)),
        notes=d.get('notes', ''),
    )
    for it in d.get('items', []):
        qty = float(it.get('quantity', 1))
        rate = float(it.get('rate', 0))
        inv.items.append(InvoiceItem(
            description=it.get('description', ''),
            quantity=qty, rate=rate, amount=round(qty * rate, 2)))

    inv.subtotal = round(sum(i.amount for i in inv.items), 2)
    inv.total    = round(inv.subtotal * (1 + inv.tax_rate / 100), 2)
    db.session.add(inv)
    db.session.commit()
    return jsonify(inv.to_dict()), 201


@app.route('/api/invoices/<int:iid>', methods=['PUT'])
@login_required
def update_invoice(iid):
    inv = Invoice.query.filter_by(id=iid, user_id=current_uid()).first_or_404()
    d   = request.json or {}
    for f in ('client_name', 'client_email', 'date', 'due_date', 'status', 'notes'):
        if f in d:
            setattr(inv, f, d[f])
    if 'tax_rate' in d:
        inv.tax_rate = float(d['tax_rate'])
    if 'items' in d:
        for old in inv.items:
            db.session.delete(old)
        for it in d['items']:
            qty = float(it.get('quantity', 1))
            rate = float(it.get('rate', 0))
            inv.items.append(InvoiceItem(
                description=it.get('description', ''),
                quantity=qty, rate=rate, amount=round(qty * rate, 2)))
        inv.subtotal = round(sum(i.amount for i in inv.items), 2)
        inv.total    = round(inv.subtotal * (1 + inv.tax_rate / 100), 2)
    db.session.commit()
    return jsonify(inv.to_dict())


@app.route('/api/invoices/<int:iid>', methods=['DELETE'])
@login_required
def delete_invoice(iid):
    inv = Invoice.query.filter_by(id=iid, user_id=current_uid()).first_or_404()
    db.session.delete(inv)
    db.session.commit()
    return jsonify({'ok': True})


# ── Expenses ───────────────────────────────────────────────────────────────

@app.route('/api/expenses', methods=['GET'])
@login_required
def get_expenses():
    exps = Expense.query.filter_by(user_id=current_uid()).order_by(Expense.date.desc()).all()
    return jsonify([e.to_dict() for e in exps])


@app.route('/api/expenses', methods=['POST'])
@login_required
def create_expense():
    d = request.json or {}
    e = Expense(
        user_id=current_uid(),
        date=d.get('date', date.today().isoformat()),
        vendor=d.get('vendor', ''),
        description=d.get('description', ''),
        amount=float(d.get('amount', 0)),
        category=d.get('category', 'Uncategorized'),
        status=d.get('status', 'pending'),
    )
    db.session.add(e)
    db.session.commit()
    return jsonify(e.to_dict()), 201


@app.route('/api/expenses/<int:eid>', methods=['PUT'])
@login_required
def update_expense(eid):
    e = Expense.query.filter_by(id=eid, user_id=current_uid()).first_or_404()
    d = request.json or {}
    for f in ('date', 'vendor', 'description', 'category', 'status'):
        if f in d:
            setattr(e, f, d[f])
    if 'amount' in d:
        e.amount = float(d['amount'])
    db.session.commit()
    return jsonify(e.to_dict())


@app.route('/api/expenses/<int:eid>', methods=['DELETE'])
@login_required
def delete_expense(eid):
    e = Expense.query.filter_by(id=eid, user_id=current_uid()).first_or_404()
    db.session.delete(e)
    db.session.commit()
    return jsonify({'ok': True})


# ── Employees ──────────────────────────────────────────────────────────────

@app.route('/api/employees', methods=['GET'])
@login_required
def get_employees():
    emps = Employee.query.filter_by(user_id=current_uid()).order_by(Employee.name).all()
    return jsonify([e.to_dict() for e in emps])


@app.route('/api/employees', methods=['POST'])
@login_required
def create_employee():
    d = request.json or {}
    e = Employee(
        user_id=current_uid(),
        name=d.get('name', ''),
        title=d.get('title', ''),
        email=d.get('email', ''),
        pay_type=d.get('pay_type', 'salary'),
        pay_rate=float(d.get('pay_rate', 0)),
        start_date=d.get('start_date', date.today().isoformat()),
        status=d.get('status', 'active'),
    )
    db.session.add(e)
    db.session.commit()
    return jsonify(e.to_dict()), 201


@app.route('/api/employees/<int:eid>', methods=['PUT'])
@login_required
def update_employee(eid):
    e = Employee.query.filter_by(id=eid, user_id=current_uid()).first_or_404()
    d = request.json or {}
    for f in ('name', 'title', 'email', 'pay_type', 'start_date', 'status'):
        if f in d:
            setattr(e, f, d[f])
    if 'pay_rate' in d:
        e.pay_rate = float(d['pay_rate'])
    db.session.commit()
    return jsonify(e.to_dict())


@app.route('/api/employees/<int:eid>', methods=['DELETE'])
@login_required
def delete_employee(eid):
    e = Employee.query.filter_by(id=eid, user_id=current_uid()).first_or_404()
    db.session.delete(e)
    db.session.commit()
    return jsonify({'ok': True})


# ── Payroll ────────────────────────────────────────────────────────────────

@app.route('/api/payroll', methods=['GET'])
@login_required
def get_payroll():
    runs = PayrollRun.query.filter_by(user_id=current_uid()).order_by(PayrollRun.pay_date.desc()).all()
    return jsonify([r.to_dict() for r in runs])


@app.route('/api/payroll', methods=['POST'])
@login_required
def create_payroll_run():
    d   = request.json or {}
    uid = current_uid()
    employees   = Employee.query.filter_by(user_id=uid, status='active').all()
    total_gross = sum(e.pay_rate for e in employees)
    total_net   = round(total_gross * 0.75, 2)
    run = PayrollRun(
        user_id=uid,
        period_start=d.get('period_start', ''),
        period_end=d.get('period_end', ''),
        pay_date=d.get('pay_date', date.today().isoformat()),
        status='draft',
        total_gross=round(total_gross, 2),
        total_net=total_net,
    )
    db.session.add(run)
    db.session.commit()
    return jsonify(run.to_dict()), 201


@app.route('/api/payroll/<int:rid>', methods=['PUT'])
@login_required
def update_payroll_run(rid):
    r = PayrollRun.query.filter_by(id=rid, user_id=current_uid()).first_or_404()
    d = request.json or {}
    for f in ('period_start', 'period_end', 'pay_date', 'status'):
        if f in d:
            setattr(r, f, d[f])
    db.session.commit()
    return jsonify(r.to_dict())


# ── Marketing ──────────────────────────────────────────────────────────────

@app.route('/api/marketing', methods=['GET'])
@login_required
def get_marketing():
    camps = MarketingCampaign.query.filter_by(user_id=current_uid()).order_by(MarketingCampaign.created_at.desc()).all()
    return jsonify([c.to_dict() for c in camps])


@app.route('/api/marketing', methods=['POST'])
@login_required
def create_campaign():
    d = request.json or {}
    c = MarketingCampaign(
        user_id=current_uid(),
        name=d.get('name', ''),
        channel=d.get('channel', ''),
        start_date=d.get('start_date', date.today().isoformat()),
        end_date=d.get('end_date', ''),
        budget=float(d.get('budget', 0)),
        spent=float(d.get('spent', 0)),
        impressions=int(d.get('impressions', 0)),
        clicks=int(d.get('clicks', 0)),
        leads=int(d.get('leads', 0)),
        revenue=float(d.get('revenue', 0)),
        status=d.get('status', 'active'),
    )
    db.session.add(c)
    db.session.commit()
    return jsonify(c.to_dict()), 201


@app.route('/api/marketing/<int:cid>', methods=['PUT'])
@login_required
def update_campaign(cid):
    c = MarketingCampaign.query.filter_by(id=cid, user_id=current_uid()).first_or_404()
    d = request.json or {}
    for f in ('name', 'channel', 'start_date', 'end_date', 'status'):
        if f in d:
            setattr(c, f, d[f])
    for f in ('budget', 'spent', 'revenue'):
        if f in d:
            setattr(c, f, float(d[f]))
    for f in ('impressions', 'clicks', 'leads'):
        if f in d:
            setattr(c, f, int(d[f]))
    db.session.commit()
    return jsonify(c.to_dict())


@app.route('/api/marketing/<int:cid>', methods=['DELETE'])
@login_required
def delete_campaign(cid):
    c = MarketingCampaign.query.filter_by(id=cid, user_id=current_uid()).first_or_404()
    db.session.delete(c)
    db.session.commit()
    return jsonify({'ok': True})


# ── Google Ads ─────────────────────────────────────────────────────────────

_GA_SCOPES = ['https://www.googleapis.com/auth/adwords']
_GA_REDIRECT = 'http://localhost:5000/oauth/google-ads/callback'


@app.route('/oauth/google-ads/start')
@login_required
def google_ads_oauth_start():
    """Kick off the OAuth2 flow — redirect user to Google consent screen."""
    client_id     = request.args.get('client_id', '').strip()
    client_secret = request.args.get('client_secret', '').strip()
    dev_token     = request.args.get('developer_token', '').strip()
    customer_id   = request.args.get('customer_id', '').strip()

    if not client_id or not client_secret:
        return 'Missing client_id or client_secret', 400

    # Stash in session so callback can retrieve them
    session['ga_pending'] = {
        'client_id':       client_id,
        'client_secret':   client_secret,
        'developer_token': dev_token,
        'customer_id':     customer_id,
    }

    try:
        from google_auth_oauthlib.flow import Flow   # type: ignore
        flow = Flow.from_client_config(
            {'web': {
                'client_id':     client_id,
                'client_secret': client_secret,
                'auth_uri':      'https://accounts.google.com/o/oauth2/auth',
                'token_uri':     'https://oauth2.googleapis.com/token',
                'redirect_uris': [_GA_REDIRECT],
            }},
            scopes=_GA_SCOPES,
            redirect_uri=_GA_REDIRECT,
        )
        auth_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            prompt='consent',
        )
        session['ga_oauth_state'] = state
        return redirect(auth_url)
    except Exception as e:
        return f'OAuth setup failed: {e}', 500


@app.route('/oauth/google-ads/callback')
@login_required
def google_ads_oauth_callback():
    """Google redirects here after user approves. Exchange code → refresh token."""
    pending = session.pop('ga_pending', {})
    state   = session.pop('ga_oauth_state', None)

    if not pending:
        return redirect('/?ga_error=session_expired#googleads')

    try:
        from google_auth_oauthlib.flow import Flow   # type: ignore
        import os
        os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'   # allow http for localhost

        flow = Flow.from_client_config(
            {'web': {
                'client_id':     pending['client_id'],
                'client_secret': pending['client_secret'],
                'auth_uri':      'https://accounts.google.com/o/oauth2/auth',
                'token_uri':     'https://oauth2.googleapis.com/token',
                'redirect_uris': [_GA_REDIRECT],
            }},
            scopes=_GA_SCOPES,
            redirect_uri=_GA_REDIRECT,
            state=state,
        )
        flow.fetch_token(authorization_response=request.url)
        credentials = flow.credentials

        # Get user email
        try:
            import requests as req_lib   # type: ignore
            userinfo = req_lib.get(
                'https://www.googleapis.com/oauth2/v3/userinfo',
                headers={'Authorization': f'Bearer {credentials.token}'}
            ).json()
            email = userinfo.get('email', '')
        except Exception:
            email = ''

        uid  = current_uid()
        conn = GoogleAdsConnection.query.filter_by(user_id=uid).first()
        if not conn:
            conn = GoogleAdsConnection(user_id=uid)
            db.session.add(conn)
        conn.developer_token = pending.get('developer_token', '')
        conn.client_id       = pending['client_id']
        conn.client_secret   = pending['client_secret']
        conn.refresh_token   = credentials.refresh_token or ''
        conn.customer_id     = pending.get('customer_id', '')
        conn.login_email     = email
        conn.connected_at    = datetime.utcnow()
        db.session.commit()

        return redirect('/?ga_connected=1#googleads')
    except Exception as e:
        return redirect(f'/?ga_error={str(e)[:80]}#googleads')


@app.route('/admin/google-ads/status')
@login_required
def google_ads_status():
    uid  = current_uid()
    conn = GoogleAdsConnection.query.filter_by(user_id=uid).first()
    if conn:
        return jsonify({'connected': True, 'email': conn.login_email,
                        'customer_id': conn.customer_id})
    return jsonify({'connected': False})


@app.route('/api/google-ads/connect', methods=['POST'])
@login_required
def google_ads_connect():
    d   = request.json or {}
    uid = current_uid()
    conn = GoogleAdsConnection.query.filter_by(user_id=uid).first()
    if not conn:
        conn = GoogleAdsConnection(user_id=uid)
        db.session.add(conn)
    conn.developer_token = d.get('developer_token', '')
    conn.client_id       = d.get('client_id', '')
    conn.client_secret   = d.get('client_secret', '')
    conn.refresh_token   = d.get('refresh_token', '')
    conn.customer_id     = d.get('customer_id', '')
    conn.login_email     = d.get('login_email', '')
    conn.connected_at    = datetime.utcnow()
    db.session.commit()
    return jsonify({'ok': True, 'connection': conn.to_dict()})


@app.route('/api/google-ads/disconnect', methods=['DELETE'])
@login_required
def google_ads_disconnect():
    uid  = current_uid()
    conn = GoogleAdsConnection.query.filter_by(user_id=uid).first()
    if conn:
        db.session.delete(conn)
        db.session.commit()
    return jsonify({'ok': True})


_GA_MOCK_CAMPAIGNS = [
    {'id': 'demo-1', 'name': 'Brand Awareness — Search', 'status': 'ENABLED',
     'budget_daily': 50.0, 'impressions': 12400, 'clicks': 620, 'conversions': 18,
     'cost': 310.0, 'ctr': 5.0, 'cpc': 0.50, 'conv_rate': 2.9},
    {'id': 'demo-2', 'name': 'Product Launch — Display', 'status': 'ENABLED',
     'budget_daily': 75.0, 'impressions': 45000, 'clicks': 900, 'conversions': 31,
     'cost': 540.0, 'ctr': 2.0, 'cpc': 0.60, 'conv_rate': 3.4},
    {'id': 'demo-3', 'name': 'Retargeting — Shopping', 'status': 'PAUSED',
     'budget_daily': 30.0, 'impressions': 8200, 'clicks': 410, 'conversions': 22,
     'cost': 184.0, 'ctr': 5.0, 'cpc': 0.45, 'conv_rate': 5.4},
]


@app.route('/api/google-ads/campaigns', methods=['GET'])
@login_required
def get_google_ads_campaigns():
    uid  = current_uid()
    conn = GoogleAdsConnection.query.filter_by(user_id=uid).first()

    # Saved campaigns from our DB
    saved = GoogleAdsCampaign.query.filter_by(user_id=uid).order_by(GoogleAdsCampaign.created_at.desc()).all()
    saved_list = [c.to_dict() for c in saved]

    if not conn:
        return jsonify({'demo': True, 'campaigns': _GA_MOCK_CAMPAIGNS, 'saved': saved_list})

    # Connection exists — attempt real API; fall back to mock on any error
    try:
        from google.ads.googleads.client import GoogleAdsClient  # type: ignore
        config = {
            'developer_token': conn.developer_token,
            'client_id':       conn.client_id,
            'client_secret':   conn.client_secret,
            'refresh_token':   conn.refresh_token,
            'login_customer_id': conn.customer_id,
            'use_proto_plus':  True,
        }
        ga_client  = GoogleAdsClient.load_from_dict(config)
        ga_service = ga_client.get_service('GoogleAdsService')
        query = ('SELECT campaign.id, campaign.name, campaign.status, '
                 'campaign_budget.amount_micros, metrics.impressions, '
                 'metrics.clicks, metrics.conversions, metrics.cost_micros, '
                 'metrics.ctr, metrics.average_cpc '
                 'FROM campaign WHERE segments.date DURING LAST_30_DAYS')
        stream = ga_service.search_stream(customer_id=conn.customer_id, query=query)
        live = []
        for batch in stream:
            for row in batch.results:
                m = row.metrics
                live.append({
                    'id':          str(row.campaign.id),
                    'name':        row.campaign.name,
                    'status':      row.campaign.status.name,
                    'budget_daily': round(row.campaign_budget.amount_micros / 1e6, 2),
                    'impressions': int(m.impressions),
                    'clicks':      int(m.clicks),
                    'conversions': round(m.conversions, 1),
                    'cost':        round(m.cost_micros / 1e6, 2),
                    'ctr':         round(m.ctr * 100, 2),
                    'cpc':         round(m.average_cpc / 1e6, 2),
                    'conv_rate':   round(m.conversions / m.clicks * 100, 2) if m.clicks else 0,
                })
        return jsonify({'demo': False, 'campaigns': live, 'saved': saved_list})
    except ImportError:
        return jsonify({'demo': True, 'campaigns': _GA_MOCK_CAMPAIGNS, 'saved': saved_list,
                        'note': 'google-ads library not installed; showing demo data.'})
    except Exception as ex:
        return jsonify({'demo': True, 'campaigns': _GA_MOCK_CAMPAIGNS, 'saved': saved_list,
                        'note': str(ex)})


@app.route('/api/google-ads/campaigns', methods=['POST'])
@login_required
def create_google_ads_campaign():
    d   = request.json or {}
    uid = current_uid()
    c = GoogleAdsCampaign(
        user_id      = uid,
        name         = d.get('name', ''),
        objective    = d.get('objective', ''),
        demographic  = d.get('demographic', ''),
        budget_daily = float(d.get('budget_daily', 0)),
        start_date   = d.get('start_date', date.today().isoformat()),
        end_date     = d.get('end_date', ''),
        status       = d.get('status', 'DRAFT'),
        headline1    = d.get('headline1', '')[:30],
        headline2    = d.get('headline2', '')[:30],
        headline3    = d.get('headline3', '')[:30],
        description1 = d.get('description1', '')[:90],
        description2 = d.get('description2', '')[:90],
        final_url    = d.get('final_url', ''),
    )
    db.session.add(c)
    db.session.commit()
    return jsonify(c.to_dict()), 201


@app.route('/api/google-ads/campaigns/<int:cid>', methods=['DELETE'])
@login_required
def delete_google_ads_campaign(cid):
    c = GoogleAdsCampaign.query.filter_by(id=cid, user_id=current_uid()).first_or_404()
    db.session.delete(c)
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/google-ads/suggest')
@login_required
def google_ads_suggest():
    uid  = current_uid()
    txns = Transaction.query.filter_by(user_id=uid).all()

    income_cats  = {}
    expense_cats = {}
    for t in txns:
        if t.tx_type == 'income':
            income_cats[t.category]  = income_cats.get(t.category, 0)  + abs(t.amount)
        else:
            expense_cats[t.category] = expense_cats.get(t.category, 0) + abs(t.amount)

    top_income  = sorted(income_cats.items(),  key=lambda x: x[1], reverse=True)[:5]
    top_expense = sorted(expense_cats.items(), key=lambda x: x[1], reverse=True)[:5]

    context = (
        f"Business financial summary:\n"
        f"Top revenue categories: {', '.join(f'{k} (${v:,.0f})' for k, v in top_income) or 'N/A'}\n"
        f"Top expense categories: {', '.join(f'{k} (${v:,.0f})' for k, v in top_expense) or 'N/A'}\n"
        f"Total transactions: {len(txns)}"
    )

    prompt = f"""{context}

Based on this small business's financial data, suggest 3 targeted Google Ads campaigns.
Return ONLY a valid JSON array with exactly 3 objects, each with these keys:
- name: campaign name (string, max 60 chars)
- objective: one of "Sales", "Leads", "Website traffic", "Brand awareness"
- demographic: target audience description (string, max 80 chars)
- budget_daily: suggested daily budget in USD (number)
- headline1, headline2, headline3: ad headlines (strings, max 30 chars each)
- description1, description2: ad descriptions (strings, max 90 chars each)
- rationale: brief 1-sentence reason for this campaign

JSON:"""

    try:
        msg  = get_client().messages.create(
            model='claude-haiku-4-5', max_tokens=2048,
            messages=[{'role': 'user', 'content': prompt}])
        text = msg.content[0].text.strip()
        s, e = text.find('['), text.rfind(']') + 1
        if s >= 0 and e > s:
            suggestions = json.loads(text[s:e])
            return jsonify({'success': True, 'suggestions': suggestions})
        return jsonify({'success': True, 'suggestions': []})
    except Exception as ex:
        return jsonify({'error': str(ex)}), 500


# ── Clear All Data ─────────────────────────────────────────────────────────

@app.route('/api/data/all', methods=['DELETE'])
@login_required
def clear_all_data():
    uid = current_uid()
    for model in [InvoiceItem, Invoice, Transaction, Expense, Employee, PayrollRun, MarketingCampaign, PayrollRecord, PayStub, GoogleAdsCampaign]:
        if hasattr(model, 'user_id'):
            model.query.filter_by(user_id=uid).delete()
    db.session.commit()
    return jsonify({'ok': True, 'message': 'All data cleared.'})


# ── File Upload & Parsing ──────────────────────────────────────────────────

import pandas as pd
import base64

@app.route('/upload', methods=['POST'])
@login_required
def upload():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'No file selected'}), 400
    try:
        if file.filename.lower().endswith('.csv'):
            df = pd.read_csv(file)
        elif file.filename.lower().endswith(('.xlsx', '.xls')):
            df = pd.read_excel(file)
        else:
            return jsonify({'error': 'Please upload a CSV or Excel file'}), 400

        df.columns = [str(c).strip() for c in df.columns]
        cols = df.columns.tolist()

        col_map = {}
        for col in cols:
            cl = col.lower()
            if not col_map.get('date') and any(x in cl for x in ['date','posted','time','period']):
                col_map['date'] = col
            if not col_map.get('description') and any(x in cl for x in ['desc','merchant','payee','name','memo','detail','narr','product','item','category','type','note']):
                col_map['description'] = col
            if not col_map.get('amount') and any(x in cl for x in ['amount','debit','credit','sum','total','value','price','sale','revenue','cost','profit']):
                col_map['amount'] = col

        col_map.setdefault('date',        cols[0])
        col_map.setdefault('description', cols[1] if len(cols) > 1 else cols[0])
        col_map.setdefault('amount',      cols[2] if len(cols) > 2 else cols[0])

        transactions = []
        for _, row in df.iterrows():
            desc = str(row.get(col_map['description'], '')).strip()
            if desc and desc.lower() != 'nan':
                transactions.append({
                    'date':        str(row.get(col_map['date'], '')).strip(),
                    'description': desc,
                    'amount':      str(row.get(col_map['amount'], '')).strip(),
                })

        return jsonify({'success': True, 'transactions': transactions[:500],
                        'total': len(transactions), 'columns': cols})
    except Exception as e:
        return jsonify({'error': f'Error parsing file: {e}'}), 500


# ── AI Clean ───────────────────────────────────────────────────────────────

@app.route('/clean', methods=['POST'])
@login_required
def clean_data():
    data             = request.json or {}
    raw_transactions = data.get('transactions', [])
    if not raw_transactions:
        return jsonify({'error': 'No data to clean'}), 400

    columns     = list(raw_transactions[0].keys()) if raw_transactions else []
    sample_rows = raw_transactions[:8]
    agent_notes, issues, data_type, mapping = "", [], "other", {}

    try:
        mapping, agent_notes, issues, data_type = run_excel_agent(columns, sample_rows)
    except Exception as ex:
        print(f'Excel agent error: {ex}')

    date_field   = mapping.get('date_column')   or mapping.get('date_field')
    desc_field   = mapping.get('description_column') or mapping.get('description_field', '')
    amount_field = mapping.get('amount_column') or mapping.get('amount_field', '')

    if raw_transactions:
        keys = list(raw_transactions[0].keys())
        if not date_field   and len(keys) > 0: date_field   = keys[0]
        if not desc_field   and len(keys) > 1: desc_field   = keys[1]
        if not amount_field and len(keys) > 2: amount_field = keys[2]

    cleaned, seen, skipped = [], set(), 0
    for t in raw_transactions:
        date_val = str(t.get(date_field,   '') if date_field   else '').strip()
        desc     = str(t.get(desc_field,   '') if desc_field   else '').strip()
        amount   = str(t.get(amount_field, '') if amount_field else '').strip()

        if not desc or desc.lower() in ('nan', 'none', '', 'null', 'n/a'):
            skipped += 1; continue

        amt_clean = amount.replace('$','').replace(',','').replace('£','').replace('€','').replace(' ','').strip()
        try:
            val = float(amt_clean)
        except ValueError:
            nums = re.findall(r'-?\d+\.?\d*', amt_clean)
            if nums:
                amt_clean = nums[0]; val = float(amt_clean)
            else:
                skipped += 1; continue

        if math.isnan(val) or val == 0:
            skipped += 1; continue

        key = f'{date_val}|{desc.lower()}|{amt_clean}'
        if key in seen:
            skipped += 1; continue
        seen.add(key)

        cleaned.append({
            'date':        date_val if date_val and date_val.lower() not in ('nan','none','null') else 'N/A',
            'description': desc,
            'amount':      amt_clean,
        })

    return jsonify({'success': True, 'transactions': cleaned,
                    'original_count': len(raw_transactions), 'cleaned_count': len(cleaned),
                    'removed': skipped, 'issues': issues, 'data_type': data_type,
                    'agent_notes': agent_notes,
                    'fields_detected': {'date': date_field, 'description': desc_field, 'amount': amount_field}})


# ── AI Categorize ──────────────────────────────────────────────────────────

@app.route('/categorize', methods=['POST'])
@login_required
def categorize():
    data         = request.json or {}
    transactions = data.get('transactions', [])
    id_offset    = int(data.get('id_offset', 0))
    if not transactions:
        return jsonify({'error': 'No transactions'}), 400

    tx_text = '\n'.join(f"{i+1}. {t['date']} | {t['description']} | {t['amount']}"
                        for i, t in enumerate(transactions))

    prompt = f"""You are an expert bookkeeper. Categorize each transaction into exactly one of:
{', '.join(CATEGORIES)}

Reply with ONLY a valid JSON array:
[{{"id": 1, "category": "Revenue / Sales", "type": "income"}}, ...]

"type" must be "income" or "expense". IDs must match the numbers in the list.

Transactions:
{tx_text}

JSON:"""

    try:
        msg  = get_client().messages.create(model='claude-haiku-4-5', max_tokens=4096,
                                            messages=[{'role': 'user', 'content': prompt}])
        text = msg.content[0].text.strip()
        s, e = text.find('['), text.rfind(']') + 1
        if s >= 0 and e > s:
            parsed = json.loads(text[s:e])
            for item in parsed:
                item['id'] = id_offset + item['id']
            return jsonify({'success': True, 'categories': parsed})
        return jsonify({'success': True, 'categories': []})
    except Exception as ex:
        print(f'Categorize error: {ex}')
        return jsonify({'error': str(ex)}), 500


# ── AI Chat ────────────────────────────────────────────────────────────────

@app.route('/chat', methods=['POST'])
@login_required
def chat():
    data    = request.json or {}
    message = data.get('message', '').strip()
    module  = data.get('module', 'dashboard')
    history = data.get('history', [])
    if not message:
        return jsonify({'error': 'No message'}), 400

    context = build_db_context(module)

    system = f"""You are BooksAI, an expert AI bookkeeping assistant for small businesses.
You have full access to the user's financial data below. Be specific with dollar amounts.
Format currency as USD with $ and commas. Keep responses concise and actionable.
Current module: {module}

--- FINANCIAL DATA ---
{context}
--- END DATA ---"""

    messages = [{'role': h['role'], 'content': h['content']} for h in history[-12:]]
    messages.append({'role': 'user', 'content': message})

    try:
        resp = get_client().messages.create(model='claude-sonnet-4-5', max_tokens=1024,
                                            system=system, messages=messages)
        return jsonify({'success': True, 'reply': resp.content[0].text})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Context builder ────────────────────────────────────────────────────────

def build_db_context(module='dashboard'):
    uid  = current_uid()
    txns = Transaction.query.filter_by(user_id=uid).all()
    if not txns:
        return 'No transactions recorded yet.'

    income   = sum(abs(t.amount) for t in txns if t.tx_type == 'income')
    expenses = sum(abs(t.amount) for t in txns if t.tx_type == 'expense')
    by_cat   = {}
    for t in txns:
        by_cat[t.category] = by_cat.get(t.category, 0) + abs(t.amount)

    lines = [
        f'Total Transactions: {len(txns)}',
        f'Total Income:   ${income:,.2f}',
        f'Total Expenses: ${expenses:,.2f}',
        f'Net P&L:        ${income - expenses:,.2f}',
        '', 'Top Categories:',
    ]
    for cat, amt in sorted(by_cat.items(), key=lambda x: x[1], reverse=True)[:10]:
        lines.append(f'  {cat}: ${amt:,.2f}')

    if module == 'invoices':
        invs = Invoice.query.filter_by(user_id=uid).all()
        lines += ['', f'Invoices: {len(invs)} total',
                  f'  Outstanding: ${sum(i.total for i in invs if i.status in ("draft","sent")):,.2f}',
                  f'  Paid: ${sum(i.total for i in invs if i.status=="paid"):,.2f}']
    elif module == 'payroll':
        emps = Employee.query.filter_by(user_id=uid, status='active').all()
        lines += ['', f'Active Employees: {len(emps)}',
                  f'  Total Monthly Payroll: ${sum(e.pay_rate for e in emps):,.2f}']
    elif module == 'marketing':
        camps = MarketingCampaign.query.filter_by(user_id=uid).all()
        lines += ['', f'Marketing Campaigns: {len(camps)}',
                  f'  Total Spend: ${sum(c.spent for c in camps):,.2f}',
                  f'  Total Revenue: ${sum(c.revenue for c in camps):,.2f}']

    lines.append('\nRecent 20 transactions:')
    recent = sorted(txns, key=lambda t: t.date, reverse=True)[:20]
    for t in recent:
        lines.append(f'  {t.date} | {t.description} | ${t.amount:,.2f} | {t.category}')

    return '\n'.join(lines)


# ── Receipt / Pay-Stub Vision ─────────────────────────────────────────────

_PAYSTUB_PROMPT = """\
You are an expert payroll data extractor. Read this document carefully and extract all \
payroll / pay-stub information.

Return ONLY a valid JSON object with these keys (use null for anything not found):
{
  "doc_type": "paystub" | "invoice" | "receipt" | "bank_statement" | "other",
  "employer_name": string,
  "employee_name": string,
  "employee_id": string,
  "department": string,
  "job_title": string,
  "pay_date": "YYYY-MM-DD",
  "period_start": "YYYY-MM-DD",
  "period_end": "YYYY-MM-DD",
  "regular_pay": number,
  "overtime_pay": number,
  "other_pay": number,
  "gross_pay": number,
  "federal_tax": number,
  "state_tax": number,
  "ss_tax": number,
  "medicare_tax": number,
  "other_deductions": number,
  "total_deductions": number,
  "net_pay": number,
  "notes": "any other relevant details as a short string",
  "transactions": [
    {"date":"YYYY-MM-DD","description":"...","amount":"0.00"}
  ]
}

For pay stubs: populate all pay/deduction fields and set transactions to [].
For receipts/bank statements: set transactions to a list of line items and leave pay fields null.
Return ONLY the JSON object — no markdown fences, no explanation.
"""


def _call_claude_vision(file_bytes: bytes, media_type: str, filename: str):
    """Send a document or image to Claude and return parsed JSON."""
    client = get_client()

    is_pdf = media_type == 'application/pdf' or filename.lower().endswith('.pdf')

    if is_pdf:
        content_block = {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.standard_b64encode(file_bytes).decode("utf-8"),
            },
        }
    else:
        safe_mt = media_type if media_type in ('image/jpeg','image/png','image/gif','image/webp') else 'image/jpeg'
        content_block = {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": safe_mt,
                "data": base64.standard_b64encode(file_bytes).decode("utf-8"),
            },
        }

    resp = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=2048,
        messages=[{
            "role": "user",
            "content": [
                content_block,
                {"type": "text", "text": _PAYSTUB_PROMPT},
            ],
        }],
    )

    raw = resp.content[0].text.strip()
    # Strip accidental markdown fences
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


@app.route('/receipt', methods=['POST'])
@login_required
def receipt_upload():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'No file selected'}), 400

    allowed_ext = {'.pdf', '.jpg', '.jpeg', '.png', '.gif', '.webp'}
    ext = os.path.splitext(file.filename.lower())[1]
    if ext not in allowed_ext:
        return jsonify({'error': f'Unsupported file type "{ext}". Use PDF, JPG, or PNG.'}), 400

    try:
        file_bytes = file.read()
        media_type = file.content_type or ('application/pdf' if ext == '.pdf' else 'image/jpeg')
        extracted = _call_claude_vision(file_bytes, media_type, file.filename)
    except Exception as ex:
        return jsonify({'error': f'AI extraction failed: {ex}'}), 500

    doc_type = extracted.get('doc_type', 'other')

    # ── If it looks like a pay stub, save it ──────────────────────────────
    if doc_type == 'paystub':
        def _f(key):
            v = extracted.get(key)
            try: return float(v) if v not in (None, '', 'null') else 0.0
            except: return 0.0

        stub = PayStub(
            user_id       = current_uid(),
            filename      = file.filename,
            employer_name = extracted.get('employer_name') or '',
            employee_name = extracted.get('employee_name') or '',
            employee_id   = extracted.get('employee_id')   or '',
            department    = extracted.get('department')    or '',
            job_title     = extracted.get('job_title')     or '',
            pay_date      = extracted.get('pay_date')      or '',
            period_start  = extracted.get('period_start')  or '',
            period_end    = extracted.get('period_end')    or '',
            regular_pay       = _f('regular_pay'),
            overtime_pay      = _f('overtime_pay'),
            other_pay         = _f('other_pay'),
            gross_pay         = _f('gross_pay'),
            federal_tax       = _f('federal_tax'),
            state_tax         = _f('state_tax'),
            ss_tax            = _f('ss_tax'),
            medicare_tax      = _f('medicare_tax'),
            other_deductions  = _f('other_deductions'),
            total_deductions  = _f('total_deductions'),
            net_pay           = _f('net_pay'),
            notes             = str(extracted.get('notes') or ''),
        )
        db.session.add(stub)
        db.session.commit()
        return jsonify({'success': True, 'doc_type': 'paystub', 'paystub': stub.to_dict()})

    # ── Otherwise return as generic transactions ──────────────────────────
    txns = extracted.get('transactions') or []
    if not txns:
        # Build a single-row summary from whatever we found
        amount = extracted.get('net_pay') or extracted.get('gross_pay') or 0
        if amount:
            txns = [{
                'date':        extracted.get('pay_date') or date.today().isoformat(),
                'description': extracted.get('employer_name') or file.filename,
                'amount':      str(amount),
            }]
    return jsonify({'success': True, 'doc_type': doc_type, 'transactions': txns})


# ── Pay-Stub CRUD ──────────────────────────────────────────────────────────

@app.route('/api/paystubs', methods=['GET'])
@login_required
def get_paystubs():
    stubs = PayStub.query.filter_by(user_id=current_uid()).order_by(PayStub.pay_date.desc()).all()
    return jsonify([s.to_dict() for s in stubs])


@app.route('/api/paystubs/<int:sid>', methods=['DELETE'])
@login_required
def delete_paystub(sid):
    s = PayStub.query.filter_by(id=sid, user_id=current_uid()).first_or_404()
    db.session.delete(s)
    db.session.commit()
    return jsonify({'ok': True})


# ── Unified Expenses (cross-source) ───────────────────────────────────────

@app.route('/api/expenses/all', methods=['GET'])
@login_required
def expenses_all():
    """Return every expense-type record from ALL sources: Expense table,
    expense Transactions, and PayrollRecords — with optional filters."""
    uid = current_uid()

    date_from = request.args.get('date_from', '')   # YYYY-MM-DD
    date_to   = request.args.get('date_to',   '')
    category  = request.args.get('category',  '')   # single category or blank = all
    search    = request.args.get('search',    '').lower()
    source    = request.args.get('source',    '')   # 'expense' | 'transaction' | 'payroll' | '' = all

    unified = []

    # ── Source 1: manual Expense rows ──────────────────────────────────────
    if not source or source == 'expense':
        exps = Expense.query.filter_by(user_id=uid).all()
        for e in exps:
            unified.append({
                'id':          f'e-{e.id}',
                'source':      'Manual Expense',
                'source_key':  'expense',
                'date':        e.date or '',
                'vendor':      e.vendor or '',
                'description': e.description or '',
                'amount':      e.amount or 0,
                'category':    e.category or 'Uncategorized',
                'status':      e.status or 'pending',
            })

    # ── Source 2: Transactions with tx_type='expense' ──────────────────────
    if not source or source == 'transaction':
        txns = Transaction.query.filter_by(user_id=uid, tx_type='expense').all()
        for t in txns:
            unified.append({
                'id':          f't-{t.id}',
                'source':      'Imported Transaction',
                'source_key':  'transaction',
                'date':        t.date or '',
                'vendor':      '',
                'description': t.description or '',
                'amount':      abs(t.amount or 0),
                'category':    t.category or 'Uncategorized',
                'status':      'imported',
            })

    # ── Source 3: PayrollRecords (each row = a payroll expense) ────────────
    if not source or source == 'payroll':
        precs = PayrollRecord.query.filter_by(user_id=uid).all()
        for r in precs:
            pay_amt = r.total_pay or r.regular_pay or 0
            if pay_amt <= 0:
                continue
            yr = r.pay_year or 0
            date_str = f'{yr}-01-01' if yr else ''
            dept = r.department_name or r.department_no or 'Unknown Department'
            unified.append({
                'id':          f'p-{r.id}',
                'source':      'Payroll Record',
                'source_key':  'payroll',
                'date':        date_str,
                'vendor':      dept,
                'description': f'{r.job_title or "Employee"} — {r.employment_type or ""} {r.job_status or ""}'.strip(' —'),
                'amount':      round(pay_amt, 2),
                'category':    'Payroll / Wages',
                'status':      (r.job_status or 'active').lower(),
            })

    # ── Apply filters ──────────────────────────────────────────────────────
    if date_from:
        unified = [r for r in unified if r['date'] >= date_from]
    if date_to:
        unified = [r for r in unified if r['date'] <= date_to]
    if category:
        unified = [r for r in unified if r['category'] == category]
    if search:
        unified = [r for r in unified if
                   search in r['description'].lower() or
                   search in r['vendor'].lower() or
                   search in r['category'].lower() or
                   search in r['source'].lower()]

    # Sort by date desc
    unified.sort(key=lambda r: r['date'] or '0000', reverse=True)

    # Summary stats
    total_amount  = round(sum(r['amount'] for r in unified), 2)
    by_category   = {}
    by_source     = {}
    for r in unified:
        by_category[r['category']] = by_category.get(r['category'], 0) + r['amount']
        by_source[r['source']]     = by_source.get(r['source'], 0)     + r['amount']
    top_cats = sorted(by_category.items(), key=lambda x: x[1], reverse=True)[:8]

    return jsonify({
        'rows':          unified,
        'count':         len(unified),
        'total_amount':  total_amount,
        'by_source':     [{'source': k, 'total': round(v, 2)} for k, v in by_source.items()],
        'top_categories':[{'category': k, 'total': round(v, 2)} for k, v in top_cats],
    })


# ── Payroll CSV Upload & Analytics ────────────────────────────────────────

# Column aliases: map various CSV header names → our canonical field names
_PAY_COL_MAP = {
    # pay year
    'pay_year': 'pay_year', 'year': 'pay_year', 'fiscal_year': 'pay_year',
    # department number
    'department_no': 'department_no', 'dept_no': 'department_no', 'dept_num': 'department_no',
    'department_number': 'department_no', 'departm_no': 'department_no',
    # department name
    'department_title': 'department_name', 'department': 'department_name',
    'dept_title': 'department_name', 'dept_name': 'department_name',
    'departm_title': 'department_name',
    # job class
    'job_class_no': 'job_class_no', 'job_class': 'job_class_no', 'job_class_num': 'job_class_no',
    'job_clas_no': 'job_class_no',
    # job title
    'job_title': 'job_title', 'title': 'job_title', 'position': 'job_title',
    'job_class_title': 'job_title',
    # employment type
    'employment_type': 'employment_type', 'employ_type': 'employment_type',
    'emp_type': 'employment_type', 'employmt_type': 'employment_type',
    # job status
    'job_status': 'job_status', 'status': 'job_status', 'job_stat': 'job_status',
    # mou
    'mou': 'mou', 'bargaining_unit': 'mou',
    # pay columns
    'regular_pay': 'regular_pay', 'base_pay': 'regular_pay', 'regular': 'regular_pay',
    'reg_pay': 'regular_pay',
    'overtime_pay': 'overtime_pay', 'ot_pay': 'overtime_pay', 'overtime': 'overtime_pay',
    'other_pay': 'other_pay', 'other': 'other_pay',
    'total_pay': 'total_pay', 'gross_pay': 'total_pay', 'total': 'total_pay',
    'benefits': 'benefits', 'total_benefits': 'benefits',
    'total_pay_benefits': 'total_pay_benefits', 'total_compensation': 'total_pay_benefits',
    'total_comp': 'total_pay_benefits',
}


def _map_payroll_cols(df_columns):
    """Return {canonical_field: actual_csv_column} mapping."""
    result = {}
    for col in df_columns:
        key = col.strip().lower().replace(' ', '_').replace('-', '_')
        canonical = _PAY_COL_MAP.get(key)
        if canonical and canonical not in result:
            result[canonical] = col
    return result


def _safe_float(val):
    try:
        return float(str(val).replace('$', '').replace(',', '').strip())
    except Exception:
        return 0.0


def _safe_int(val):
    try:
        return int(float(str(val).strip()))
    except Exception:
        return 0


@app.route('/upload/payroll', methods=['POST'])
@login_required
def upload_payroll():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'No file selected'}), 400

    try:
        fname = file.filename.lower()
        if fname.endswith('.csv'):
            df = pd.read_csv(file, low_memory=False)
        elif fname.endswith(('.xlsx', '.xls')):
            df = pd.read_excel(file)
        else:
            return jsonify({'error': 'Please upload a CSV or Excel file'}), 400

        df.columns = [str(c).strip() for c in df.columns]
        mapping = _map_payroll_cols(df.columns)

        # Cap at 50,000 rows for performance
        df_capped = df.head(50000)
        total = len(df_capped)

        # Build all rows (canonical fields)
        all_rows = []
        for _, row in df_capped.iterrows():
            rec = {}
            for canon, col in mapping.items():
                rec[canon] = str(row.get(col, '')).strip()
            all_rows.append(rec)

        # First 20 rows for preview
        preview = all_rows[:20]

        return jsonify({
            'success': True,
            'total': total,
            'columns': df.columns.tolist(),
            'mapping': mapping,
            'preview': preview,
            'allRows': all_rows,
        })
    except Exception as e:
        return jsonify({'error': f'Error parsing file: {e}'}), 500


@app.route('/api/payroll-records/import', methods=['POST'])
@login_required
def payroll_bulk_import():
    """Accept raw CSV rows (as list of dicts with canonical field names) and store them."""
    payload = request.json or {}
    rows    = payload.get('rows', [])
    uid     = current_uid()

    # optional: clear existing records for this user first
    if payload.get('replace', False):
        PayrollRecord.query.filter_by(user_id=uid).delete()

    added = 0
    for r in rows:
        rec = PayrollRecord(
            user_id         = uid,
            pay_year        = _safe_int(r.get('pay_year', 0)),
            department_no   = str(r.get('department_no',   '')).strip()[:50],
            department_name = str(r.get('department_name', '')).strip()[:200],
            job_class_no    = str(r.get('job_class_no',    '')).strip()[:50],
            job_title       = str(r.get('job_title',       '')).strip()[:200],
            employment_type = str(r.get('employment_type', '')).strip()[:50],
            job_status      = str(r.get('job_status',      '')).strip()[:50],
            mou             = str(r.get('mou',             '')).strip()[:50],
            regular_pay         = _safe_float(r.get('regular_pay', 0)),
            overtime_pay        = _safe_float(r.get('overtime_pay', 0)),
            other_pay           = _safe_float(r.get('other_pay', 0)),
            total_pay           = _safe_float(r.get('total_pay', 0)),
            benefits            = _safe_float(r.get('benefits', 0)),
            total_pay_benefits  = _safe_float(r.get('total_pay_benefits', 0)),
        )
        db.session.add(rec)
        added += 1

    db.session.commit()
    return jsonify({'ok': True, 'added': added})


@app.route('/api/payroll-records', methods=['GET'])
@login_required
def get_payroll_records():
    uid  = current_uid()
    recs = PayrollRecord.query.filter_by(user_id=uid).all()
    return jsonify([r.to_dict() for r in recs])


@app.route('/api/payroll-records', methods=['DELETE'])
@login_required
def clear_payroll_records():
    PayrollRecord.query.filter_by(user_id=current_uid()).delete()
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/payroll/analytics', methods=['GET'])
@login_required
def payroll_analytics():
    uid  = current_uid()
    recs = PayrollRecord.query.filter_by(user_id=uid).all()

    if not recs:
        return jsonify({'empty': True})

    total_records   = len(recs)
    total_pay       = round(sum(r.total_pay or r.regular_pay for r in recs), 2)
    total_benefits  = round(sum(r.benefits for r in recs), 2)
    total_comp      = round(sum(r.total_pay_benefits or (r.total_pay + r.benefits) for r in recs), 2)
    avg_pay         = round(total_pay / total_records, 2) if total_records else 0
    total_overtime  = round(sum(r.overtime_pay for r in recs), 2)

    # By department (top 10 by total pay)
    dept_pay = {}
    for r in recs:
        dept = r.department_name or r.department_no or 'Unknown'
        dept_pay[dept] = dept_pay.get(dept, 0) + (r.total_pay or r.regular_pay or 0)
    top_depts = sorted(dept_pay.items(), key=lambda x: x[1], reverse=True)[:10]

    # Employment type breakdown
    emp_type_counts = {}
    for r in recs:
        t = r.employment_type or 'Unknown'
        emp_type_counts[t] = emp_type_counts.get(t, 0) + 1

    # Job status breakdown
    status_counts = {}
    for r in recs:
        s = r.job_status or 'Unknown'
        status_counts[s] = status_counts.get(s, 0) + 1

    # Top job titles by avg pay (min 3 occurrences)
    title_totals = {}
    title_counts = {}
    for r in recs:
        t = r.job_title or 'Unknown'
        title_totals[t] = title_totals.get(t, 0) + (r.total_pay or r.regular_pay or 0)
        title_counts[t] = title_counts.get(t, 0) + 1
    title_avgs = [(t, round(title_totals[t] / title_counts[t], 2))
                  for t in title_totals if title_counts[t] >= 3]
    top_titles = sorted(title_avgs, key=lambda x: x[1], reverse=True)[:10]

    # Pay by year
    year_pay = {}
    for r in recs:
        yr = r.pay_year or 0
        if yr:
            year_pay[yr] = year_pay.get(yr, 0) + (r.total_pay or r.regular_pay or 0)
    years_sorted = sorted(year_pay.items())

    # Pay breakdown (regular vs overtime vs other) — totals
    total_regular = round(sum(r.regular_pay for r in recs), 2)
    total_ot      = round(sum(r.overtime_pay for r in recs), 2)
    total_other   = round(sum(r.other_pay for r in recs), 2)

    # Unique years, departments
    years       = sorted({r.pay_year for r in recs if r.pay_year})
    departments = sorted({r.department_name or r.department_no for r in recs if r.department_name or r.department_no})

    return jsonify({
        'empty': False,
        'total_records': total_records,
        'total_pay': total_pay,
        'total_benefits': total_benefits,
        'total_comp': total_comp,
        'avg_pay': avg_pay,
        'total_overtime': total_overtime,
        'total_regular': total_regular,
        'total_ot': total_ot,
        'total_other': total_other,
        'top_departments': [{'name': k, 'total': round(v, 2)} for k, v in top_depts],
        'employment_types': [{'type': k, 'count': v} for k, v in emp_type_counts.items()],
        'job_statuses':     [{'status': k, 'count': v} for k, v in status_counts.items()],
        'top_job_titles':   [{'title': t, 'avg_pay': p} for t, p in top_titles],
        'pay_by_year':      [{'year': yr, 'total': round(p, 2)} for yr, p in years_sorted],
        'years': years,
        'departments': departments,
    })


# ── Password change ────────────────────────────────────────────────────────

@app.route('/api/me/password', methods=['POST'])
@login_required
def change_password():
    u  = User.query.get(current_uid())
    d  = request.json or {}
    current_pw = d.get('current_password', '')
    new_pw     = d.get('new_password', '')
    if not current_pw or not new_pw:
        return jsonify({'error': 'All fields are required.'}), 400
    if not u.check_password(current_pw):
        return jsonify({'error': 'Current password is incorrect.'}), 400
    if len(new_pw) < 8:
        return jsonify({'error': 'New password must be at least 8 characters.'}), 400
    u.set_password(new_pw)
    db.session.commit()
    return jsonify({'ok': True})


# ── Stripe Billing ─────────────────────────────────────────────────────────

def _stripe():
    """Return configured stripe module or None if not set up."""
    key = os.environ.get('STRIPE_SECRET_KEY', '').strip()
    if not key:
        return None
    try:
        import stripe as _s
        _s.api_key = key
        return _s
    except ImportError:
        return None


@app.route('/billing')
@login_required
def billing_page():
    """Self-service: redirect logged-in user to Stripe Checkout."""
    s           = _stripe()
    price_id    = os.environ.get('STRIPE_PRICE_ID', '')
    u           = User.query.get(current_uid())
    if not s or not price_id:
        # Stripe not configured — bounce back to app
        return redirect('/?msg=billing_unavailable')
    sess = s.checkout.Session.create(
        mode='subscription',
        line_items=[{'price': price_id, 'quantity': 1}],
        customer_email=u.email,
        client_reference_id=str(u.id),
        metadata={'user_id': str(u.id)},
        success_url=request.host_url + 'billing/success?session_id={CHECKOUT_SESSION_ID}',
        cancel_url=request.host_url,
    )
    return redirect(sess.url)


@app.route('/billing/success')
def billing_success():
    return '''<!DOCTYPE html><html><head><title>Payment Successful — BooksAI</title>
<style>body{font-family:-apple-system,sans-serif;background:#0d1117;color:#e6edf3;display:flex;
align-items:center;justify-content:center;min-height:100vh;text-align:center;margin:0}
.card{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:48px 40px;max-width:420px}
h1{color:#10b981;font-size:22px;margin:16px 0 8px}p{color:#7d8590;font-size:14px;margin-bottom:24px}
a{color:#10b981;text-decoration:none;font-weight:600;font-size:14px}</style></head>
<body><div class="card"><div style="font-size:52px">🎉</div>
<h1>Payment Successful!</h1>
<p>Your BooksAI subscription is now active. Welcome aboard!</p>
<a href="/">← Go to Dashboard</a></div></body></html>'''


@app.route('/billing/webhook', methods=['POST'])
def billing_webhook():
    s              = _stripe()
    webhook_secret = os.environ.get('STRIPE_WEBHOOK_SECRET', '')
    if not s:
        return jsonify({'error': 'Stripe not configured'}), 400
    payload = request.get_data()
    sig     = request.headers.get('Stripe-Signature', '')
    try:
        event = s.Webhook.construct_event(payload, sig, webhook_secret)
    except Exception:
        return jsonify({'error': 'Invalid webhook signature'}), 400

    ev_type = event['type']
    obj     = event['data']['object']

    if ev_type == 'checkout.session.completed':
        uid = (obj.get('client_reference_id') or
               obj.get('metadata', {}).get('user_id'))
        if uid:
            u = User.query.get(int(uid))
            if u:
                u.plan               = 'active'
                u.stripe_customer_id = obj.get('customer', '')
                u.stripe_sub_id      = obj.get('subscription', '')
                db.session.commit()

    elif ev_type in ('customer.subscription.deleted', 'invoice.payment_failed'):
        cid = obj.get('customer', '')
        if cid:
            u = User.query.filter_by(stripe_customer_id=cid).first()
            if u and not u.is_admin:
                u.plan = 'suspended'
                db.session.commit()

    return jsonify({'ok': True})


@app.route('/admin/users/<int:uid>/payment-link', methods=['POST'])
@admin_required
def admin_payment_link(uid):
    """Admin: generate a Stripe Checkout URL for a specific client."""
    s        = _stripe()
    price_id = os.environ.get('STRIPE_PRICE_ID', '')
    if not s or not price_id:
        return jsonify({'error': 'Stripe not configured. Add STRIPE_SECRET_KEY and STRIPE_PRICE_ID to your env vars.'}), 400
    u    = User.query.get_or_404(uid)
    sess = s.checkout.Session.create(
        mode='subscription',
        line_items=[{'price': price_id, 'quantity': 1}],
        customer_email=u.email,
        client_reference_id=str(u.id),
        metadata={'user_id': str(u.id)},
        success_url=request.host_url + 'billing/success?session_id={CHECKOUT_SESSION_ID}',
        cancel_url=request.host_url + 'admin',
    )
    return jsonify({'url': sess.url})


# ── Init ───────────────────────────────────────────────────────────────────

with app.app_context():
    db.create_all()

    # SQLite migration: add user_id columns to existing tables
    import sqlite3
    conn = sqlite3.connect(db_path)
    for tbl in ['transaction', 'invoice', 'expense', 'employee', 'payroll_run', 'marketing_campaign']:
        try:
            conn.execute(f'ALTER TABLE "{tbl}" ADD COLUMN user_id INTEGER')
        except Exception:
            pass  # column already exists
    for col_def in [('user', 'stripe_customer_id', 'TEXT DEFAULT ""'),
                    ('user', 'stripe_sub_id',       'TEXT DEFAULT ""')]:
        try:
            conn.execute(f'ALTER TABLE "{col_def[0]}" ADD COLUMN {col_def[1]} {col_def[2]}')
        except Exception:
            pass
    conn.commit()
    conn.close()

    # Bootstrap admin account from env vars if no users exist
    if User.query.count() == 0:
        admin_email = os.environ.get('ADMIN_EMAIL', 'admin@booksai.app')
        admin_pw    = os.environ.get('ADMIN_PASSWORD', 'changeme123')
        admin       = User(email=admin_email, name='Admin', is_admin=True, plan='active')
        admin.set_password(admin_pw)
        db.session.add(admin)
        db.session.commit()
        print(f'[BooksAI] Admin account created: {admin_email}')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)
