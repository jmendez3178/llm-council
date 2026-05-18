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


# ── AI Categorization ──────────────────────────────────────────────────────

@app.route('/categorize', methods=['POST'])
def categorize():
    data         = request.json or {}
    transactions = data.get('transactions', [])
    if not transactions:
        return jsonify({'error': 'No transactions'}), 400

    results = []
    batch_size = 80

    for batch_start in range(0, len(transactions), batch_size):
        batch = transactions[batch_start:batch_start + batch_size]
        tx_text = '\n'.join(
            f"{batch_start + i + 1}. {t['date']} | {t['description']} | {t['amount']}"
            for i, t in enumerate(batch)
        )

        prompt = f"""You are an expert bookkeeper. Categorize each transaction into exactly one of:
{', '.join(CATEGORIES)}

Reply with ONLY a valid JSON array — no explanation, no markdown:
[{{"id": 1, "category": "Revenue / Sales", "type": "income"}}, ...]

"type" must be "income" or "expense".

Transactions:
{tx_text}

JSON:"""

        try:
            msg = get_client().messages.create(
                model='claude-3-5-haiku-20241022',
                max_tokens=4096,
                messages=[{'role': 'user', 'content': prompt}]
            )
            text = msg.content[0].text.strip()
            s, e = text.find('['), text.rfind(']') + 1
            if s >= 0 and e > s:
                results.extend(json.loads(text[s:e]))
        except Exception as ex:
            print(f'Categorize error: {ex}')

    return jsonify({'success': True, 'categories': results})


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
            model='claude-3-5-sonnet-20241022',
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
