from flask import Flask, request, jsonify, render_template, session, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import anthropic, json, math, os, re, secrets
from datetime import datetime, date
import calendar

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
    for model in [InvoiceItem, Invoice, Transaction, Expense, Employee, PayrollRun, MarketingCampaign]:
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


# ── Clear All Data ─────────────────────────────────────────────────────────

@app.route('/api/data/all', methods=['DELETE'])
@login_required
def clear_all_data():
    uid = current_uid()
    for model in [InvoiceItem, Invoice, Transaction, Expense, Employee, PayrollRun, MarketingCampaign]:
        if hasattr(model, 'user_id'):
            model.query.filter_by(user_id=uid).delete()
        else:
            # InvoiceItem has no user_id; delete via invoices already handled by cascade
            pass
    db.session.commit()
    return jsonify({'ok': True, 'message': 'All data cleared.'})


# ── File Upload & Parsing ──────────────────────────────────────────────────

import pandas as pd

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
