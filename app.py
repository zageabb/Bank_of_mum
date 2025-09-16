import os
import json
import math
import calendar
from datetime import date, datetime
from flask import Flask, render_template, request, redirect, url_for, send_from_directory, abort

app = Flask(__name__)

DATA_DIR = os.path.join(app.root_path, 'data')
IGNORED_LOAN_IDS = {"alice", "bob"}
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
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


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
        if not filename.endswith('.json'):
            continue

        loan_id = os.path.splitext(filename)[0]
        if loan_id.lower() in IGNORED_LOAN_IDS:
            continue

        path = os.path.join(DATA_DIR, filename)
        with open(path) as f:
            loan = json.load(f)

        loan['id'] = loan_id
        payments = loan.get('payments') or []
        loan['payments'] = payments

        principal, annual_rate, months, payment = _resolve_loan_terms(loan)
        loan['principal'] = principal
        loan['interest_rate'] = annual_rate

        amortization = generate_amortization_schedule({
            **loan,
            'principal': principal,
            'interest_rate': annual_rate,
            'months': months,
            'payment_per_month': payment,
        })

        loan['months'] = months if months else 0.0
        loan['payment_per_month'] = payment if payment else 0.0
        loan['balance'] = round(loan['principal'] - sum(_coerce_float(p.get('amount')) for p in payments), 2)
        loan['total_expected_repayment'] = round(sum(row['payment'] for row in amortization), 2) if amortization else 0.0

        loans.append(loan)
    return loans


def load_loan(loan_id):
    path = os.path.join(DATA_DIR, f"{loan_id}.json")
    with open(path) as f:
        loan = json.load(f)
    loan['id'] = loan_id
    loan['payments'] = loan.get('payments') or []

    principal, annual_rate, months, payment = _resolve_loan_terms(loan)
    loan['principal'] = principal
    loan['interest_rate'] = annual_rate
    loan['months'] = months if months else 0.0
    loan['payment_per_month'] = payment if payment else 0.0

    amortization = generate_amortization_schedule({
        **loan,
        'principal': principal,
        'interest_rate': annual_rate,
        'months': months,
        'payment_per_month': payment,
    })
    loan['total_expected_repayment'] = round(sum(row['payment'] for row in amortization), 2) if amortization else 0.0
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


def _resolve_loan_terms(loan):
    principal = _coerce_float(loan.get('principal', 0))
    annual_rate = _coerce_float(loan.get('interest_rate', 0))
    months = _coerce_months(loan.get('months', 0))
    payment = _coerce_float(loan.get('payment_per_month', 0))

    if principal <= 0:
        return principal, annual_rate, 0.0, 0.0

    if months <= 0 and payment > 0:
        months = calculate_months(principal, annual_rate, payment)
    if payment <= 0 and months > 0:
        payment = calculate_monthly_payment(principal, annual_rate, months)

    return principal, annual_rate, months, payment


def _group_payments_by_period(payments, start_date, total_periods):
    grouped = {}
    if not payments:
        return grouped

    for payment in payments:
        amount = _coerce_float(payment.get('amount'))
        if amount == 0:
            continue

        payment_date = _parse_iso_date(payment.get('date')) or start_date
        months_delta = (payment_date.year - start_date.year) * 12 + (payment_date.month - start_date.month)
        period_index = months_delta + 1
        if period_index < 1:
            period_index = 1
        elif period_index > total_periods:
            period_index = total_periods

        grouped[period_index] = grouped.get(period_index, 0.0) + amount

    return grouped


def generate_amortization_schedule(loan):
    principal, annual_rate, months, payment = _resolve_loan_terms(loan)

    if principal <= 0 or months <= 0 or payment <= 0:
        return []

    monthly_rate = annual_rate / 12 / 100 if annual_rate else 0
    start_date = _schedule_start_date(loan)
    total_periods = max(int(math.ceil(months)), 1)
    payments_by_period = _group_payments_by_period(loan.get('payments', []), start_date, total_periods)
    balance = principal
    schedule = []

    for period in range(1, total_periods + 1):
        begin_balance = balance
        interest = begin_balance * monthly_rate if monthly_rate else 0.0
        payment_amount = payment

        # Ensure the final period clears the balance.
        if period == total_periods:
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

        actual_payment = payments_by_period.get(period)
        if actual_payment is None:
            actual_payment = payment_amount
        else:
            actual_payment = round(actual_payment, 2)

        schedule.append({
            'number': period,
            'date': _add_months(start_date, period - 1).isoformat(),
            'begin_balance': round(begin_balance, 2),
            'payment': payment_amount,
            'actual_payment': actual_payment,
            'interest': interest,
            'principal': principal_component,
            'end_balance': max(end_balance, 0.0),
            'is_final': period == total_periods
        })

        balance = max(end_balance, 0.0)

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
        months_value = request.form.get('months')
        payment_value = request.form.get('payment')

        months = float(months_value) if months_value else None
        payment = float(payment_value) if payment_value else None

        if months is not None and months <= 0:
            months = None
        if payment is not None and payment <= 0:
            payment = None

        if months and not payment:
            payment = calculate_monthly_payment(principal, rate, months)
        elif payment and not months:
            months = calculate_months(principal, rate, payment)
        elif not months and not payment:
            months = 12.0
            payment = calculate_monthly_payment(principal, rate, months)

        if months is not None:
            months = round(float(months), 2)
        if payment is not None:
            payment = round(float(payment), 2)

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
        months_value = request.form.get('months')
        payment_value = request.form.get('payment')

        months = float(months_value) if months_value else None
        payment = float(payment_value) if payment_value else None

        if months is not None and months <= 0:
            months = None
        if payment is not None and payment <= 0:
            payment = None

        if months and not payment:
            payment = calculate_monthly_payment(principal, rate, months)
        elif payment and not months:
            months = calculate_months(principal, rate, payment)
        elif not months and not payment:
            months = loan.get('months', 0) or 0.0
            payment = loan.get('payment_per_month', 0) or 0.0

        if months is not None:
            months = round(float(months), 2)
        if payment is not None:
            payment = round(float(payment), 2)

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

    amortization_schedule = generate_amortization_schedule(loan)
    expected_payment_info = None
    actual_payment_info = None
    if amortization_schedule:
        last_row = amortization_schedule[-1]
        expected_payment_info = {
            'amount': last_row['payment'],
            'date': last_row['date']
        }
        actual_payment_info = {
            'amount': last_row['actual_payment'],
            'date': last_row['date']
        }

    return render_template(
        'loan_details.html',
        loan=loan,
        payments=payment_rows,
        balance=round(balance, 2),
        amortization_schedule=amortization_schedule,
        expected_payment_info=expected_payment_info,
        actual_payment_info=actual_payment_info
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
