import os
import json
from datetime import date
from flask import Flask, render_template, request, redirect, url_for, send_from_directory

app = Flask(__name__)

DATA_DIR = os.path.join(app.root_path, 'data')
os.makedirs(DATA_DIR, exist_ok=True)

def load_loans():
    loans = []
    for filename in os.listdir(DATA_DIR):
        if filename.endswith('.json'):
            path = os.path.join(DATA_DIR, filename)
            with open(path) as f:
                loan = json.load(f)
            loan_id = os.path.splitext(filename)[0]
            loan['id'] = loan_id
            loan['balance'] = loan['principal'] - sum(p['amount'] for p in loan.get('payments', []))
            loans.append(loan)
    return loans


def load_loan(loan_id):
    path = os.path.join(DATA_DIR, f"{loan_id}.json")
    with open(path) as f:
        loan = json.load(f)
    loan['id'] = loan_id
    return loan


def save_loan(loan):
    path = os.path.join(DATA_DIR, f"{loan['id']}.json")
    data = {k: v for k, v in loan.items() if k != 'id'}
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)


@app.route('/')
def index():
    loans = load_loans()
    return render_template('index.html', loans=loans)


@app.route('/loan/<loan_id>')
def loan_details(loan_id):
    loan = load_loan(loan_id)
    balance = loan['principal']
    payment_rows = []
    for p in loan.get('payments', []):
        balance -= p['amount']
        payment_rows.append({
            'date': p['date'],
            'amount': p['amount'],
            'balance': balance
        })
    return render_template('loan_details.html', loan=loan, payments=payment_rows, balance=balance)


@app.route('/loan/<loan_id>/payment', methods=['GET', 'POST'])
def add_payment(loan_id):
    loan = load_loan(loan_id)
    if request.method == 'POST':
        amount = float(request.form['amount'])
        pay_date = request.form['date'] or date.today().isoformat()
        loan.setdefault('payments', []).append({'date': pay_date, 'amount': amount})
        save_loan(loan)
        return redirect(url_for('loan_details', loan_id=loan_id))
    today = date.today().isoformat()
    return render_template('add_payment.html', loan=loan, today=today)


@app.route('/loan/<loan_id>/download')
def download_loan(loan_id):
    return send_from_directory(DATA_DIR, f"{loan_id}.json", as_attachment=True)


if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5003, debug=True)
