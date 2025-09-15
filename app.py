import os
import json
import math
from datetime import date
from flask import Flask, render_template, request, redirect, url_for, send_from_directory

app = Flask(__name__)

DATA_DIR = os.path.join(app.root_path, 'data')
os.makedirs(DATA_DIR, exist_ok=True)


def calculate_monthly_payment(principal, annual_rate, months):
    """Calculate the payment per month for a compound interest loan."""
    r = annual_rate / 12 / 100
    if r == 0:
        return principal / months
    return principal * r / (1 - (1 + r) ** -months)


def calculate_months(principal, annual_rate, payment):
    """Calculate number of months needed to pay off a loan."""
    r = annual_rate / 12 / 100
    if r == 0:
        return principal / payment
    return math.log(payment / (payment - principal * r)) / math.log(1 + r)

def load_loans():
    loans = []
    for filename in os.listdir(DATA_DIR):
        if filename.endswith('.json'):
            path = os.path.join(DATA_DIR, filename)
            with open(path) as f:
                loan = json.load(f)
            loan_id = os.path.splitext(filename)[0]
            loan['id'] = loan_id
            loan.setdefault('interest_rate', 0)
            loan.setdefault('months', 0)
            loan.setdefault('payment_per_month', 0)
            loan['balance'] = loan['principal'] - sum(p['amount'] for p in loan.get('payments', []))
            loan['total_expected_repayment'] = loan['payment_per_month'] * loan['months']
            loans.append(loan)
    return loans


def load_loan(loan_id):
    path = os.path.join(DATA_DIR, f"{loan_id}.json")
    with open(path) as f:
        loan = json.load(f)
    loan['id'] = loan_id
    loan.setdefault('interest_rate', 0)
    loan.setdefault('months', 0)
    loan.setdefault('payment_per_month', 0)
    loan['total_expected_repayment'] = loan['payment_per_month'] * loan['months']
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


@app.route('/loan/add', methods=['GET', 'POST'])
def add_loan():
    if request.method == 'POST':
        child = request.form['child']
        principal = float(request.form['principal'])
        rate = float(request.form['rate'])
        months = request.form.get('months')
        payment = request.form.get('payment')

        months = int(months) if months else None
        payment = float(payment) if payment else None

        if months and not payment:
            payment = calculate_monthly_payment(principal, rate, months)
        elif payment and not months:
            months = math.ceil(calculate_months(principal, rate, payment))
        elif not months and not payment:
            months = 12
            payment = calculate_monthly_payment(principal, rate, months)

        loan_id = child.lower().replace(' ', '_')
        loan = {
            'id': loan_id,
            'child': child,
            'principal': principal,
            'interest_rate': rate,
            'months': months,
            'payment_per_month': payment,
            'payments': []
        }
        save_loan(loan)
        return redirect(url_for('index'))
    return render_template('add_loan.html')


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
