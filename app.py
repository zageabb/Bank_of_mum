import os
import json
import math
import calendar
from datetime import date, datetime
from flask import Flask, render_template, request, redirect, url_for, send_from_directory, abort

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

def _coerce_months(value):
    if value in (None, ""):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _coerce_float(value):
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def load_loans():
    loans = []
    for filename in os.listdir(DATA_DIR):
        if filename.endswith('.json'):
            path = os.path.join(DATA_DIR, filename)
            with open(path) as f:
                loan = json.load(f)
            loan_id = os.path.splitext(filename)[0]
            loan['id'] = loan_id
            loan['principal'] = _coerce_float(loan.get('principal', 0))
            loan['interest_rate'] = _coerce_float(loan.get('interest_rate', 0))
            loan['months'] = _coerce_months(loan.get('months', 0))
            loan['payment_per_month'] = _coerce_float(loan.get('payment_per_month', 0))
            loan['balance'] = loan['principal'] - sum(p['amount'] for p in loan.get('payments', []))
            loan['total_expected_repayment'] = loan['payment_per_month'] * loan['months']
            loans.append(loan)
    return loans


def load_loan(loan_id):
    path = os.path.join(DATA_DIR, f"{loan_id}.json")
    with open(path) as f:
        loan = json.load(f)
    loan['id'] = loan_id
    loan['principal'] = _coerce_float(loan.get('principal', 0))
    loan['interest_rate'] = _coerce_float(loan.get('interest_rate', 0))
    loan['months'] = _coerce_months(loan.get('months', 0))
    loan['payment_per_month'] = _coerce_float(loan.get('payment_per_month', 0))
    loan['total_expected_repayment'] = loan['payment_per_month'] * loan['months']
    return loan


def _parse_iso_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


def _add_months(start_date, months):
    year = start_date.year + (start_date.month - 1 + months) // 12
    month = (start_date.month - 1 + months) % 12 + 1
    day = min(start_date.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _schedule_start_date(loan):
    start = _parse_iso_date(loan.get('start_date'))
    if start:
        return start
    payment_dates = [_parse_iso_date(p.get('date')) for p in loan.get('payments', [])]
    payment_dates = [d for d in payment_dates if d]
    if payment_dates:
        return min(payment_dates)
    today = date.today()
    return today.replace(day=1)


def generate_amortization_schedule(loan):
    principal = _coerce_float(loan.get('principal', 0))
    annual_rate = _coerce_float(loan.get('interest_rate', 0))
    months = _coerce_months(loan.get('months', 0))
    payment = _coerce_float(loan.get('payment_per_month', 0))

    if principal <= 0 or months <= 0 or payment <= 0:
        return []

    monthly_rate = annual_rate / 12 / 100 if annual_rate else 0
    start_date = _schedule_start_date(loan)
    balance = principal
    schedule = []

    for period in range(1, months + 1):
        begin_balance = balance
        interest = begin_balance * monthly_rate if monthly_rate else 0.0
        payment_amount = payment

        # Ensure the final period clears the balance.
        if period == months:
            payment_amount = begin_balance + interest
        elif monthly_rate and payment_amount <= interest:
            # Avoid negative amortization by at least covering the interest.
            payment_amount = interest

        principal_component = payment_amount - interest
        end_balance = begin_balance - principal_component

        interest = round(interest, 2)
        payment_amount = round(payment_amount, 2)
        principal_component = round(principal_component, 2)
        end_balance = round(end_balance, 2)

        if end_balance < 0:
            # Adjust rounding artefacts so the balance finishes at zero.
            payment_amount += end_balance
            payment_amount = round(payment_amount, 2)
            principal_component = round(payment_amount - interest, 2)
            end_balance = 0.0

        schedule.append({
            'number': period,
            'date': _add_months(start_date, period - 1).isoformat(),
            'begin_balance': round(begin_balance, 2),
            'payment': payment_amount,
            'interest': interest,
            'principal': principal_component,
            'end_balance': max(end_balance, 0.0),
            'is_final': period == months
        })

        balance = end_balance

    return schedule


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


@app.route('/loan/<loan_id>/edit', methods=['GET', 'POST'])
def edit_loan(loan_id):
    loan = load_loan(loan_id)
    if request.method == 'POST':
        principal = float(request.form.get('principal', loan['principal']))
        rate = float(request.form.get('rate', 0) or 0)
        months = request.form.get('months')
        payment = request.form.get('payment')

        months = int(months) if months else None
        payment = float(payment) if payment else None

        if months and not payment:
            payment = calculate_monthly_payment(principal, rate, months)
        elif payment and not months:
            months = math.ceil(calculate_months(principal, rate, payment))
        elif not months and not payment:
            months = loan.get('months', 0)
            payment = loan.get('payment_per_month', 0)

        loan.update({
            'principal': principal,
            'interest_rate': rate,
            'months': months,
            'payment_per_month': payment
        })
        save_loan(loan)
        return redirect(url_for('loan_details', loan_id=loan_id))
    return render_template('edit_loan.html', loan=loan)


@app.route('/loan/<loan_id>')
def loan_details(loan_id):
    loan = load_loan(loan_id)
    payments_with_index = list(enumerate(loan.get('payments', [])))
    payments_with_index.sort(key=lambda item: item[1].get('date') or '')

    balance = loan['principal']
    payment_rows = []
    for idx, payment in payments_with_index:
        amount = _coerce_float(payment.get('amount'))
        balance -= amount
        payment_rows.append({
            'date': payment.get('date'),
            'amount': amount,
            'balance': round(balance, 2),
            'comment': payment.get('comment', ''),
            'index': idx
        })

    actual_final_payment = None
    if payments_with_index:
        final_payment = payments_with_index[-1][1]
        actual_final_payment = {
            'amount': _coerce_float(final_payment.get('amount')),
            'date': final_payment.get('date')
        }

    amortization_schedule = generate_amortization_schedule(loan)
    expected_final_payment = None
    if amortization_schedule:
        last_row = amortization_schedule[-1]
        expected_final_payment = {
            'amount': last_row['payment'],
            'date': last_row['date']
        }

    return render_template(
        'loan_details.html',
        loan=loan,
        payments=payment_rows,
        balance=round(balance, 2),
        amortization_schedule=amortization_schedule,
        actual_final_payment=actual_final_payment,
        expected_final_payment=expected_final_payment
    )


@app.route('/loan/<loan_id>/payment', methods=['GET', 'POST'])
def add_payment(loan_id):
    loan = load_loan(loan_id)
    if request.method == 'POST':
        amount = float(request.form['amount'])
        pay_date = request.form['date'] or date.today().isoformat()
        comment = request.form.get('comment', '')
        loan.setdefault('payments', []).append({'date': pay_date, 'amount': amount, 'comment': comment})
        save_loan(loan)
        return redirect(url_for('loan_details', loan_id=loan_id))
    today = date.today().isoformat()
    return render_template('add_payment.html', loan=loan, today=today)


@app.route('/loan/<loan_id>/payment/<int:payment_index>/edit', methods=['GET', 'POST'])
def edit_payment(loan_id, payment_index):
    loan = load_loan(loan_id)
    payments = loan.setdefault('payments', [])
    if payment_index < 0 or payment_index >= len(payments):
        abort(404)
    payment = payments[payment_index]
    if request.method == 'POST':
        payment['amount'] = float(request.form['amount'])
        payment['date'] = request.form['date']
        payment['comment'] = request.form.get('comment', '')
        save_loan(loan)
        return redirect(url_for('loan_details', loan_id=loan_id))
    payment.setdefault('comment', '')
    return render_template('edit_payment.html', loan=loan, payment=payment)


@app.route('/loan/<loan_id>/download')
def download_loan(loan_id):
    return send_from_directory(DATA_DIR, f"{loan_id}.json", as_attachment=True)


if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5003, debug=True)
