from flask import Flask, request, jsonify, render_template
import pandas as pd
import anthropic
import json
import os

app = Flask(__name__)

def get_client():
    key = os.environ.get('ANTHROPIC_API_KEY', '').strip()
    if not key:
        raise ValueError('ANTHROPIC_API_KEY is not set. Add it in Railway → Variables.')
    return anthropic.Anthropic(api_key=key)

CATEGORIES = [
    'Revenue / Sales',
    'Cost of Goods Sold',
    'Payroll / Wages',
    'Rent / Lease',
    'Utilities',
    'Marketing / Advertising',
    'Software / Subscriptions',
    'Professional Services',
    'Travel / Transportation',
    'Meals / Entertainment',
    'Office Supplies',
    'Bank Fees / Finance Charges',
    'Taxes / Licenses',
    'Insurance',
    'Loan / Debt Payment',
    'Owner Draw / Distribution',
    'Transfer',
    'Uncategorized',
]

# ── File Upload & Parsing ──────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
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

        # Auto-detect date / description / amount columns
        col_map = {}
        for col in df.columns:
            cl = col.lower()
            if not col_map.get('date') and any(x in cl for x in ['date', 'posted', 'time', 'period']):
                col_map['date'] = col
            if not col_map.get('description') and any(x in cl for x in ['desc', 'merchant', 'payee', 'name', 'memo', 'detail', 'narr', 'product', 'item', 'category', 'type', 'note']):
                col_map['description'] = col
            if not col_map.get('amount') and any(x in cl for x in ['amount', 'debit', 'credit', 'sum', 'total', 'value', 'price', 'sale', 'revenue', 'cost', 'profit']):
                col_map['amount'] = col

        # Fall back to positional if detection fails
        cols = df.columns.tolist()
        if len(cols) >= 2:
            col_map.setdefault('date',        cols[0])
            col_map.setdefault('description', cols[1])
            col_map.setdefault('amount',      cols[2] if len(cols) > 2 else cols[1])

        transactions = []
        for _, row in df.iterrows():
            desc = str(row.get(col_map.get('description', cols[0]), '')).strip()
            if desc and desc.lower() != 'nan':
                transactions.append({
                    'date':        str(row.get(col_map.get('date',        cols[0]), '')).strip(),
                    'description': desc,
                    'amount':      str(row.get(col_map.get('amount',      cols[0]), '')).strip(),
                })

        return jsonify({
            'success':      True,
            'transactions': transactions[:500],
            'total':        len(transactions),
            'columns':      cols,
        })

    except Exception as e:
        return jsonify({'error': f'Error parsing file: {str(e)}'}), 500


# ── AI Data Cleaning Agent ────────────────────────────────────────────────

@app.route('/clean', methods=['POST'])
def clean_data():
    data = request.json or {}
    raw_transactions = data.get('transactions', [])
    if not raw_transactions:
        return jsonify({'error': 'No data to clean'}), 400

    # Send sample to Claude to understand the structure
    sample = raw_transactions[:60]
    sample_text = json.dumps(sample, indent=2)

    analysis_prompt = f"""You are a data cleaning expert for financial/sales records.

Analyze this raw business data sample and identify:
1. Which field is the DATE
2. Which field is the DESCRIPTION (product, category, merchant, item name)
3. Which field is the AMOUNT (sales value, price, revenue, transaction amount)
4. Any other useful fields (quantity, location, etc.)

Data sample:
{sample_text}

Return ONLY valid JSON, no explanation:
{{
  "date_field": "exact_field_name or null",
  "description_field": "exact_field_name",
  "amount_field": "exact_field_name",
  "extra_fields": ["field1", "field2"],
  "issues": ["issue1", "issue2"],
  "data_type": "bank_statement|sales_data|expenses|invoices|other"
}}"""

    try:
        msg = get_client().messages.create(
            model='claude-haiku-4-5',
            max_tokens=400,
            messages=[{'role': 'user', 'content': analysis_prompt}]
        )
        text = msg.content[0].text.strip()
        s, e = text.find('{'), text.rfind('}') + 1
        analysis = json.loads(text[s:e]) if s >= 0 and e > s else {}
    except Exception as ex:
        analysis = {}
        print(f'Clean analysis error: {ex}')

    date_field   = analysis.get('date_field')
    desc_field   = analysis.get('description_field', '')
    amount_field = analysis.get('amount_field', '')
    issues       = analysis.get('issues', [])
    data_type    = analysis.get('data_type', 'unknown')

    # Fallback: use first fields if detection failed
    if raw_transactions:
        keys = list(raw_transactions[0].keys())
        if not date_field   and len(keys) > 0: date_field   = keys[0]
        if not desc_field   and len(keys) > 1: desc_field   = keys[1]
        if not amount_field and len(keys) > 2: amount_field = keys[2]

    cleaned = []
    seen    = set()
    skipped = 0

    for t in raw_transactions:
        date   = str(t.get(date_field,   '') if date_field   else '').strip()
        desc   = str(t.get(desc_field,   '') if desc_field   else '').strip()
        amount = str(t.get(amount_field, '') if amount_field else '').strip()

        # Drop empty descriptions
        if not desc or desc.lower() in ('nan', 'none', '', 'null', 'n/a'):
            skipped += 1
            continue

        # Clean amount — strip currency symbols, keep numeric value
        amt_clean = amount.replace('$','').replace(',','').replace('£','').replace('€','').replace(' ','').strip()
        try:
            float(amt_clean)
        except ValueError:
            # Try to extract first number
            import re
            nums = re.findall(r'-?\d+\.?\d*', amt_clean)
            if nums:
                amt_clean = nums[0]
            else:
                skipped += 1
                continue

        # Skip zero-amount rows
        if float(amt_clean) == 0:
            skipped += 1
            continue

        # Deduplicate exact matches
        key = f'{date}|{desc.lower()}|{amt_clean}'
        if key in seen:
            skipped += 1
            continue
        seen.add(key)

        cleaned.append({
            'date':        date if date and date.lower() not in ('nan','none','null') else 'N/A',
            'description': desc,
            'amount':      amt_clean,
        })

    return jsonify({
        'success':        True,
        'transactions':   cleaned,
        'original_count': len(raw_transactions),
        'cleaned_count':  len(cleaned),
        'removed':        skipped,
        'issues':         issues,
        'data_type':      data_type,
        'fields_detected': {
            'date':   date_field,
            'description': desc_field,
            'amount': amount_field,
        }
    })


# ── AI Categorization ──────────────────────────────────────────────────────

@app.route('/categorize', methods=['POST'])
def categorize():
    data         = request.json or {}
    transactions = data.get('transactions', [])
    id_offset    = int(data.get('id_offset', 0))
    if not transactions:
        return jsonify({'error': 'No transactions'}), 400

    tx_text = '\n'.join(
        f"{i + 1}. {t['date']} | {t['description']} | {t['amount']}"
        for i, t in enumerate(transactions)
    )

    prompt = f"""You are an expert bookkeeper. Categorize each transaction into exactly one of:
{', '.join(CATEGORIES)}

Reply with ONLY a valid JSON array — no explanation, no markdown:
[{{"id": 1, "category": "Revenue / Sales", "type": "income"}}, ...]

"type" must be "income" or "expense".
IDs in your response must match the numbers in the list below exactly.

Transactions:
{tx_text}

JSON:"""

    try:
        msg = get_client().messages.create(
            model='claude-haiku-4-5',
            max_tokens=4096,
            messages=[{'role': 'user', 'content': prompt}]
        )
        text = msg.content[0].text.strip()
        s, e = text.find('['), text.rfind(']') + 1
        if s >= 0 and e > s:
            parsed = json.loads(text[s:e])
            # Offset IDs so they match global transaction indices
            for item in parsed:
                item['id'] = id_offset + item['id']
            return jsonify({'success': True, 'categories': parsed})
        return jsonify({'success': True, 'categories': []})
    except Exception as ex:
        print(f'Categorize error: {ex}')
        return jsonify({'error': str(ex)}), 500


# ── AI Chat ────────────────────────────────────────────────────────────────

@app.route('/chat', methods=['POST'])
def chat():
    data         = request.json or {}
    message      = data.get('message', '').strip()
    transactions = data.get('transactions', [])
    categorized  = data.get('categorized', [])
    history      = data.get('history', [])

    if not message:
        return jsonify({'error': 'No message'}), 400

    context = build_context(transactions, categorized)

    system = f"""You are BooksAI, an expert AI bookkeeping assistant for small businesses.
You have full access to the user's transaction data below. Be specific with dollar amounts.
Always format currency as USD with $ and commas. Keep responses concise and actionable.

--- TRANSACTION DATA ---
{context}
--- END DATA ---"""

    messages = [{'role': h['role'], 'content': h['content']} for h in history[-12:]]
    messages.append({'role': 'user', 'content': message})

    try:
        resp = get_client().messages.create(
            model='claude-sonnet-4-5',
            max_tokens=1024,
            system=system,
            messages=messages,
        )
        return jsonify({'success': True, 'reply': resp.content[0].text})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Helpers ────────────────────────────────────────────────────────────────

def build_context(transactions, categorized):
    if not transactions:
        return 'No transactions loaded.'

    cat_map = {c['id']: c for c in categorized}
    income = expenses = 0.0
    by_cat = {}

    for i, t in enumerate(transactions):
        try:
            amt = float(str(t.get('amount', '0')).replace('$', '').replace(',', '').strip())
        except Exception:
            continue
        info = cat_map.get(i + 1, {})
        tx_type  = info.get('type', 'income' if amt > 0 else 'expense')
        category = info.get('category', 'Uncategorized')
        if tx_type == 'income':
            income += abs(amt)
        else:
            expenses += abs(amt)
        by_cat[category] = by_cat.get(category, 0.0) + abs(amt)

    lines = [
        f'Transactions: {len(transactions)}',
        f'Total Income:   ${income:,.2f}',
        f'Total Expenses: ${expenses:,.2f}',
        f'Net P&L:        ${income - expenses:,.2f}',
        '',
        'Top Categories:',
    ]
    for cat, amt in sorted(by_cat.items(), key=lambda x: x[1], reverse=True)[:12]:
        lines.append(f'  {cat}: ${amt:,.2f}')

    lines.append('\nFirst 30 transactions:')
    for t in transactions[:30]:
        lines.append(f"  {t['date']} | {t['description']} | {t['amount']}")

    return '\n'.join(lines)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)
