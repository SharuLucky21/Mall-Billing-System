from flask import Flask, render_template, request, redirect, url_for, flash, session, abort, jsonify, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, logout_user, login_required, current_user, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
from functools import wraps
from config import Config
from sqlalchemy import text
import io

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    REPORTLAB_AVAILABLE = True
except Exception:
    REPORTLAB_AVAILABLE = False

app = Flask(__name__)
app.config.from_object(Config)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

# ----------------- Models -----------------

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # "admin" or "cashier"

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    barcode = db.Column(db.String(64), unique=True, nullable=False)
    price = db.Column(db.Float, nullable=False)
    stock = db.Column(db.Integer, default=0, nullable=False)
    low_stock = db.Column(db.Boolean, nullable=False, default=False)
    image_url = db.Column(db.String(255), nullable=True)

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cashier_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    total = db.Column(db.Float, nullable=False, default=0.0)
    paid_cash = db.Column(db.Float, nullable=False, default=0.0)
    change_due = db.Column(db.Float, nullable=False, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    payment_method = db.Column(db.String(20), nullable=False, default='cash')  # cash, card, upi, wallet

    cashier = db.relationship('User')

class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    unit_price = db.Column(db.Float, nullable=False)

    order = db.relationship('Order', backref=db.backref('items', lazy=True))
    product = db.relationship('Product')

class PromoCode(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(40), unique=True, nullable=False)
    discount_type = db.Column(db.String(10), nullable=False, default='percent')  # percent or fixed
    discount_value = db.Column(db.Float, nullable=False, default=0.0)
    active = db.Column(db.Boolean, nullable=False, default=True)
    expires_at = db.Column(db.DateTime, nullable=True)

# ----------------- Auth helpers -----------------

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def role_required(*roles):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                return login_manager.unauthorized()
            if current_user.role not in roles:
                abort(403)
            return fn(*args, **kwargs)
        return wrapper
    return decorator

# ----------------- DB init & seed -----------------

@app.cli.command("initdb")
def initdb_command():
    """Initialize the database and create default users/products.\n
    Usage: flask --app app initdb
    """
    db.drop_all()
    db.create_all()

    # Users
    admin = User(username="admin", role="admin")
    admin.set_password("admin123")
    cashier = User(username="cashier", role="cashier")
    cashier.set_password("cashier123")
    db.session.add_all([admin, cashier])

    # Sample products
    sample = [
        ("Men's T-Shirt", "CLOTH001", 799.00, 120, "https://images.unsplash.com/photo-1521572163474-6864f9cf17ab?w=300&h=300&fit=crop"),
        ("Women's Handbag", "ACCS001", 2499.00, 60, "https://images.unsplash.com/photo-1553062407-98eeb64c6a62?w=300&h=300&fit=crop"),
        ("Bluetooth Earbuds", "ELEC001", 1999.00, 80, "https://images.unsplash.com/photo-1606220945770-b5b6c2c55bf1?w=300&h=300&fit=crop"),
        ("Laptop 14", "ELEC002", 49999.00, 20, "https://images.unsplash.com/photo-1496181133206-80ce9b88a853?w=300&h=300&fit=crop"),
        ("Kids Sneakers", "CLOTH002", 1499.00, 50, "https://images.unsplash.com/photo-1542291026-7eec264c27ff?w=300&h=300&fit=crop"),
        ("Smartwatch", "ELEC003", 6999.00, 35, "https://images.unsplash.com/photo-1523275335684-37898b6baf30?w=300&h=300&fit=crop"),
        ("Saree Silk", "CLOTH003", 3999.00, 25, "https://images.unsplash.com/photo-1594633312681-425c7b97ccd1?w=300&h=300&fit=crop"),
    ]
    for name, barcode, price, stock, image_url in sample:
        db.session.add(Product(name=name, barcode=barcode, price=price, stock=stock, image_url=image_url))

    db.session.commit()
    print("Database initialized. Users: admin/admin123, cashier/cashier123")

# ----------------- Reports helpers -----------------

def daterange_days(n):
    today = datetime.utcnow().date()
    return [today - timedelta(days=i) for i in range(n-1, -1, -1)]

def summarize_range(days: int):
    dates = daterange_days(days)
    data = []
    for day in dates:
        start = datetime(day.year, day.month, day.day)
        end = start + timedelta(days=1)
        q = Order.query.filter(Order.created_at >= start, Order.created_at < end)
        total = sum(o.total for o in q)
        count = q.count()
        data.append({
            "date": day.strftime('%Y-%m-%d'),
            "total": round(total, 2),
            "orders": count,
        })
    return data

# ----------------- Routes -----------------

@app.route("/")
def index():
    if current_user.is_authenticated:
        if current_user.role == "admin":
            return redirect(url_for("admin_dashboard"))
        return redirect(url_for("pos"))
    return render_template("home.html")

@app.route("/home")
def home_page():
    return render_template("home.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for("index"))
        flash("Invalid credentials", "danger")
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        confirm = request.form.get("confirm", "").strip()
        role = request.form.get("role", "cashier")
        if not username or not password:
            flash("Username and password are required.", "warning")
            return redirect(url_for("register"))
        if password != confirm:
            flash("Passwords do not match.", "danger")
            return redirect(url_for("register"))
        if User.query.filter_by(username=username).first():
            flash("Username already exists.", "danger")
            return redirect(url_for("register"))
        if role not in ("cashier", "admin"):
            role = "cashier"
        user = User(username=username, role=role)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash("Account created. Please login.", "success")
        return redirect(url_for("login"))
    return render_template("register.html")

# ----------------- Admin: Dashboard & Products -----------------

@app.route("/admin")
@login_required
@role_required("admin")
def admin_dashboard():
    total_products = Product.query.count()
    total_orders = Order.query.count()
    total_sales = db.session.query(db.func.sum(Order.total)).scalar() or 0
    low_stock_count = Product.query.filter_by(low_stock=True).count()
    # Payment method breakdown
    cash_count = db.session.query(db.func.count()).select_from(Order).filter_by(payment_method='cash').scalar() or 0
    card_count = db.session.query(db.func.count()).select_from(Order).filter_by(payment_method='card').scalar() or 0
    upi_count = db.session.query(db.func.count()).select_from(Order).filter_by(payment_method='upi').scalar() or 0
    wallet_count = db.session.query(db.func.count()).select_from(Order).filter_by(payment_method='wallet').scalar() or 0
    return render_template("admin_dashboard.html",
                           total_products=total_products,
                           total_orders=total_orders,
                           total_sales=total_sales,
                           low_stock_count=low_stock_count,
                           cash_count=cash_count,
                           card_count=card_count,
                           upi_count=upi_count,
                           wallet_count=wallet_count)

@app.route("/admin/api/sales_summary")
@login_required
@role_required("admin")
def admin_sales_summary():
    range_key = request.args.get("range", "daily")
    if range_key == "daily":
        data = summarize_range(7)
    elif range_key == "weekly":
        # 12 weeks ~= 84 days, aggregate by week
        raw = summarize_range(84)
        buckets = {}
        for d in raw:
            week = datetime.strptime(d["date"], "%Y-%m-%d").strftime("%Y-W%U")
            b = buckets.setdefault(week, {"label": week, "total": 0.0, "orders": 0})
            b["total"] += d["total"]
            b["orders"] += d["orders"]
        data = [{"label": k, "total": round(v["total"], 2), "orders": v["orders"]} for k, v in sorted(buckets.items())][-12:]
        return jsonify({"range": "weekly", "series": data})
    elif range_key == "monthly":
        # 12 months aggregation
        now = datetime.utcnow()
        first = datetime(now.year, now.month, 1)
        months = []
        for i in range(11, -1, -1):
            m = first - timedelta(days=30*i)
            months.append((m.year, m.month))
        series = []
        for y, m in months:
            start = datetime(y, m, 1)
            if m == 12:
                end = datetime(y+1, 1, 1)
            else:
                end = datetime(y, m+1, 1)
            q = Order.query.filter(Order.created_at >= start, Order.created_at < end)
            total = sum(o.total for o in q)
            count = q.count()
            series.append({"label": f"{y}-{m:02d}", "total": round(total, 2), "orders": count})
        return jsonify({"range": "monthly", "series": series})
    else:
        data = summarize_range(7)
    return jsonify({"range": "daily", "series": data})

@app.route("/admin/products")
@login_required
@role_required("admin")
def products_list():
    q = request.args.get("q", "").strip()
    products = Product.query
    if q:
        products = products.filter(
            db.or_(Product.name.ilike(f"%{q}%"), Product.barcode.ilike(f"%{q}%"))
        )
    products = products.order_by(Product.name.asc()).all()
    return render_template("products.html", products=products, q=q)

@app.route("/admin/products/new", methods=["GET", "POST"])
@login_required
@role_required("admin")
def product_new():
    if request.method == "POST":
        name = request.form["name"].strip()
        barcode = request.form["barcode"].strip()
        price = float(request.form["price"])
        stock = int(request.form.get("stock", 0))
        image_url = request.form.get("image_url", "").strip()
        if Product.query.filter_by(barcode=barcode).first():
            flash("Barcode already exists.", "danger")
            return redirect(url_for("product_new"))
        db.session.add(Product(name=name, barcode=barcode, price=price, stock=stock, image_url=image_url))
        db.session.commit()
        flash("Product created.", "success")
        return redirect(url_for("products_list"))
    return render_template("product_form.html", product=None)

@app.route("/admin/products/<int:pid>/edit", methods=["GET", "POST"])
@login_required
@role_required("admin")
def product_edit(pid):
    product = Product.query.get_or_404(pid)
    if request.method == "POST":
        product.name = request.form["name"].strip()
        product.barcode = request.form["barcode"].strip()
        product.price = float(request.form["price"])
        product.stock = int(request.form.get("stock", 0))
        product.low_stock = bool(request.form.get("low_stock"))
        product.image_url = request.form.get("image_url", "").strip()
        db.session.commit()
        flash("Product updated.", "success")
        return redirect(url_for("products_list"))
    return render_template("product_form.html", product=product)

@app.route("/admin/products/<int:pid>/delete", methods=["POST"])
@login_required
@role_required("admin")
def product_delete(pid):
    product = Product.query.get_or_404(pid)
    db.session.delete(product)
    db.session.commit()
    flash("Product deleted.", "success")
    return redirect(url_for("products_list"))

# ----------------- Admin: Orders List -----------------

@app.route("/admin/orders")
@login_required
@role_required("admin")
def orders_list():
    orders = Order.query.order_by(Order.created_at.desc()).all()
    return render_template("orders.html", orders=orders)

# ----------------- Admin: Promo Codes -----------------

@app.route("/admin/promocodes")
@login_required
@role_required("admin")
def promocodes_list():
    codes = PromoCode.query.order_by(PromoCode.code.asc()).all()
    return render_template("promocodes.html", codes=codes)

@app.route("/admin/promocodes/new", methods=["GET","POST"])
@login_required
@role_required("admin")
def promocode_new():
    if request.method == "POST":
        code = request.form['code'].strip().upper()
        discount_type = request.form['discount_type']
        discount_value = float(request.form['discount_value'])
        active = bool(request.form.get('active'))
        expires_at = request.form.get('expires_at')
        if PromoCode.query.filter_by(code=code).first():
            flash("Promo code already exists.", "danger")
            return redirect(url_for('promocode_new'))
        exp_dt = datetime.strptime(expires_at, '%Y-%m-%d') if expires_at else None
        db.session.add(PromoCode(code=code, discount_type=discount_type, discount_value=discount_value, active=active, expires_at=exp_dt))
        db.session.commit()
        flash("Promo code created.", "success")
        return redirect(url_for('promocodes_list'))
    return render_template("promocode_form.html")

@app.route("/admin/promocodes/<int:pid>/toggle", methods=["POST"])
@login_required
@role_required("admin")
def promocode_toggle(pid):
    pc = PromoCode.query.get_or_404(pid)
    pc.active = not pc.active
    db.session.commit()
    return redirect(url_for('promocodes_list'))

@app.route("/admin/update-images", methods=["POST"])
@login_required
@role_required("admin")
def update_product_images():
    """Update all products with appropriate images"""
    products = Product.query.all()
    image_mapping = {
        "Men's T-Shirt": "https://images.unsplash.com/photo-1521572163474-6864f9cf17ab?w=300&h=300&fit=crop",
        "Women's Handbag": "https://images.unsplash.com/photo-1553062407-98eeb64c6a62?w=300&h=300&fit=crop",
        "Bluetooth Earbuds": "https://images.unsplash.com/photo-1606220945770-b5b6c2c55bf1?w=300&h=300&fit=crop",
        "Laptop 14": "https://images.unsplash.com/photo-1496181133206-80ce9b88a853?w=300&h=300&fit=crop",
        "Kids Sneakers": "https://images.unsplash.com/photo-1542291026-7eec264c27ff?w=300&h=300&fit=crop",
        "Smartwatch": "https://images.unsplash.com/photo-1523275335684-37898b6baf30?w=300&h=300&fit=crop",
        "Saree Silk": "https://images.unsplash.com/photo-1594633312681-425c7b97ccd1?w=300&h=300&fit=crop",
        "Apple - 1 kg": "https://images.unsplash.com/photo-1560806887-1e4cd0b6cbd6?w=300&h=300&fit=crop",
        "Milk - 1 L": "https://images.unsplash.com/photo-1550583724-b2692b85b150?w=300&h=300&fit=crop",
        "Bread - 500 g": "https://images.unsplash.com/photo-1509440159596-0249088772ff?w=300&h=300&fit=crop",
        "Toothpaste": "https://images.unsplash.com/photo-1556228720-195a672e8a03?w=300&h=300&fit=crop",
        "Shampoo 200ml": "https://images.unsplash.com/photo-1556228720-195a672e8a03?w=300&h=300&fit=crop",
        "Biscuits - 200 g": "https://images.unsplash.com/photo-1555507036-ab1f4038808a?w=300&h=300&fit=crop",
        "Rice - 5 kg": "https://images.unsplash.com/photo-1586201375761-83865001e31c?w=300&h=300&fit=crop"
    }
    updated_count = 0
    for product in products:
        if product.name in image_mapping:
            product.image_url = image_mapping[product.name]
            updated_count += 1
    db.session.commit()
    flash(f"Updated {updated_count} products with images.", "success")
    return redirect(url_for('products_list'))

# ----------------- Cashier: POS -----------------

def get_cart():
    return session.setdefault("cart", {})

def set_cart(cart):
    session["cart"] = cart
    session.modified = True

@app.route("/pos", methods=["GET", "POST"])
@login_required
@role_required("cashier", "admin")
def pos():
    cart = get_cart()

    if request.method == "POST":
        action = request.form.get("action")
        if action == "add_by_barcode":
            code = request.form.get("barcode", "").strip()
            if not code:
                flash("Scan or enter a barcode.", "warning")
            else:
                product = Product.query.filter_by(barcode=code).first()
                if not product:
                    flash("Product not found.", "danger")
                else:
                    pid = str(product.id)
                    cart[pid] = cart.get(pid, 0) + 1
                    set_cart(cart)
        elif action == "update_qty":
            pid = request.form.get("pid")
            qty = max(0, int(request.form.get("qty", 1)))
            if qty == 0:
                cart.pop(pid, None)
            else:
                cart[pid] = qty
            set_cart(cart)
        elif action == "clear":
            set_cart({})
        elif action == "mark_low":
            pid = int(request.form.get("pid"))
            product = Product.query.get_or_404(pid)
            product.low_stock = True
            db.session.commit()
            flash(f"Marked '{product.name}' as low stock.", "info")
        elif action == "unmark_low":
            pid = int(request.form.get("pid"))
            product = Product.query.get_or_404(pid)
            product.low_stock = False
            db.session.commit()
            flash(f"Unmarked '{product.name}' from low stock.", "info")
        elif action == "add_by_id":
            pid = int(request.form.get("pid"))
            product = Product.query.get(pid)
            if product:
                sid = str(product.id)
                cart[sid] = cart.get(sid, 0) + 1
                set_cart(cart)
        return redirect(url_for("pos"))

    # Build detailed cart
    items = []
    subtotal = 0.0
    for pid, qty in cart.items():
        product = Product.query.get(int(pid))
        if product:
            line_total = product.price * qty
            subtotal += line_total
            items.append({
                "id": product.id,
                "name": product.name,
                "barcode": product.barcode,
                "price": product.price,
                "qty": qty,
                "stock": product.stock,
                "line_total": line_total
            })

    # promo support
    promo_code = request.args.get('promo', '').strip().upper()
    promo_discount = 0.0
    if promo_code:
        pc = PromoCode.query.filter_by(code=promo_code, active=True).first()
        if pc and (pc.expires_at is None or pc.expires_at >= datetime.utcnow()):
            if pc.discount_type == 'percent':
                promo_discount = round(subtotal * (pc.discount_value/100.0), 2)
            else:
                promo_discount = min(subtotal, pc.discount_value)
        else:
            flash("Invalid or expired promo code.", "warning")
    total_after_discount = max(0.0, round(subtotal - promo_discount, 2))

    # Available products list for quick add
    q = request.args.get("q", "").strip()
    plist = Product.query
    if q:
        plist = plist.filter(db.or_(Product.name.ilike(f"%{q}%"), Product.barcode.ilike(f"%{q}%")))
    plist = plist.order_by(Product.name.asc()).limit(50).all()

    return render_template("pos.html", items=items, subtotal=subtotal, products=plist, q=q, promo_code=promo_code, promo_discount=promo_discount, total_after_discount=total_after_discount)

@app.route("/checkout", methods=["POST"])
@login_required
@role_required("cashier", "admin")
def checkout():
    cart = get_cart()
    if not cart:
        flash("Cart is empty.", "warning")
        return redirect(url_for("pos"))

    # Compute totals
    subtotal = 0.0
    for pid, qty in cart.items():
        product = Product.query.get(int(pid))
        if not product or product.stock < qty:
            flash(f"Insufficient stock for {product.name if product else 'Unknown'}", "danger")
            return redirect(url_for("pos"))
        subtotal += product.price * qty

    payment_method = request.form.get("payment_method", "cash")

    # Apply promo code from form
    promo_code = request.form.get('promo_code', '').strip().upper()
    promo_discount = 0.0
    if promo_code:
        pc = PromoCode.query.filter_by(code=promo_code, active=True).first()
        if pc and (pc.expires_at is None or pc.expires_at >= datetime.utcnow()):
            if pc.discount_type == 'percent':
                promo_discount = round(subtotal * (pc.discount_value/100.0), 2)
            else:
                promo_discount = min(subtotal, pc.discount_value)
        else:
            flash("Invalid or expired promo code.", "warning")
            return redirect(url_for('pos', promo=promo_code))

    total_due = max(0.0, round(subtotal - promo_discount, 2))

    # Normalize amounts
    amount_paid = 0.0
    change_due = 0.0
    if payment_method == "cash":
        amount_paid = float(request.form.get("paid_cash", 0.0))
        if amount_paid < total_due:
            flash("Paid amount is less than total.", "danger")
            return redirect(url_for("pos", promo=promo_code))
        change_due = round(amount_paid - total_due, 2)
    else:
        amount_paid = total_due
        change_due = 0.0

    # Create order
    order = Order(
        cashier_id=current_user.id,
        total=total_due,
        paid_cash=amount_paid,
        change_due=change_due,
        payment_method=payment_method,
    )
    db.session.add(order)
    db.session.flush()  # get order.id

    # Create items and update stock
    for pid, qty in cart.items():
        product = Product.query.get(int(pid))
        db.session.add(OrderItem(order_id=order.id, product_id=product.id, quantity=qty, unit_price=product.price))
        product.stock -= qty
    db.session.commit()

    set_cart({})
    return redirect(url_for("receipt", order_id=order.id))

@app.route("/receipt/<int:order_id>")
@login_required
@role_required("cashier", "admin")
def receipt(order_id):
    order = Order.query.get_or_404(order_id)
    return render_template("receipt.html", order=order)

@app.route("/receipt/<int:order_id>/pdf")
@login_required
@role_required("cashier", "admin")
def receipt_pdf(order_id):
    order = Order.query.get_or_404(order_id)
    if not REPORTLAB_AVAILABLE:
        flash("PDF generator not available on server.", "warning")
        return redirect(url_for('receipt', order_id=order.id))
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    width, height = letter
    y = height - 50
    c.setFont("Helvetica-Bold", 14)
    c.drawString(50, y, "Mall Billing - Receipt")
    y -= 20
    c.setFont("Helvetica", 10)
    c.drawString(50, y, f"Order #: {order.id}")
    y -= 14
    c.drawString(50, y, f"Date: {order.created_at.strftime('%Y-%m-%d %H:%M')}")
    y -= 14
    c.drawString(50, y, f"Cashier: {order.cashier.username}")
    y -= 14
    c.drawString(50, y, f"Payment: {order.payment_method.upper()}")
    y -= 24
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, y, "Item")
    c.drawString(300, y, "Qty")
    c.drawString(350, y, "Price")
    c.drawString(420, y, "Total")
    y -= 12
    c.setFont("Helvetica", 10)
    for it in order.items:
        if y < 80:
            c.showPage(); y = height - 50
        c.drawString(50, y, it.product.name)
        c.drawRightString(330, y, str(it.quantity))
        c.drawRightString(400, y, f"₹ {it.unit_price:.2f}")
        c.drawRightString(500, y, f"₹ {it.unit_price * it.quantity:.2f}")
        y -= 14
    y -= 10
    c.setFont("Helvetica-Bold", 11)
    c.drawRightString(500, y, f"Total: ₹ {order.total:.2f}")
    y -= 16
    c.setFont("Helvetica", 10)
    c.drawRightString(500, y, f"Paid: ₹ {order.paid_cash:.2f}")
    y -= 14
    c.drawRightString(500, y, f"Change: ₹ {order.change_due:.2f}")
    c.showPage()
    c.save()
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=f"receipt_{order.id}.pdf", mimetype="application/pdf")

# --------------- Run ---------------

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        # Lightweight migrations
        try:
            inspector = db.inspect(db.engine)
            columns_order = [c['name'] for c in inspector.get_columns('order')]
            if 'payment_method' not in columns_order:
                with db.engine.connect() as conn:
                    conn.execute(text("ALTER TABLE \"order\" ADD COLUMN payment_method VARCHAR(20) NOT NULL DEFAULT 'cash'"))
                    conn.commit()
            columns_product = [c['name'] for c in inspector.get_columns('product')]
            if 'low_stock' not in columns_product:
                with db.engine.connect() as conn:
                    conn.execute(text("ALTER TABLE product ADD COLUMN low_stock BOOLEAN NOT NULL DEFAULT 0"))
                    conn.commit()
            if 'image_url' not in columns_product:
                with db.engine.connect() as conn:
                    conn.execute(text("ALTER TABLE product ADD COLUMN image_url VARCHAR(255)"))
                    conn.commit()
                # Update existing products with images
                products = Product.query.all()
                image_mapping = {
                    "Men's T-Shirt": "https://images.unsplash.com/photo-1521572163474-6864f9cf17ab?w=300&h=300&fit=crop",
                    "Women's Handbag": "https://images.unsplash.com/photo-1553062407-98eeb64c6a62?w=300&h=300&fit=crop",
                    "Bluetooth Earbuds": "https://images.unsplash.com/photo-1606220945770-b5b6c2c55bf1?w=300&h=300&fit=crop",
                    "Laptop 14": "https://images.unsplash.com/photo-1496181133206-80ce9b88a853?w=300&h=300&fit=crop",
                    "Kids Sneakers": "https://images.unsplash.com/photo-1542291026-7eec264c27ff?w=300&h=300&fit=crop",
                    "Smartwatch": "https://images.unsplash.com/photo-1523275335684-37898b6baf30?w=300&h=300&fit=crop",
                    "Saree Silk": "https://images.unsplash.com/photo-1594633312681-425c7b97ccd1?w=300&h=300&fit=crop",
                    "Apple - 1 kg": "https://images.unsplash.com/photo-1560806887-1e4cd0b6cbd6?w=300&h=300&fit=crop",
                    "Milk - 1 L": "https://images.unsplash.com/photo-1550583724-b2692b85b150?w=300&h=300&fit=crop",
                    "Bread - 500 g": "https://images.unsplash.com/photo-1509440159596-0249088772ff?w=300&h=300&fit=crop",
                    "Toothpaste": "https://images.unsplash.com/photo-1556228720-195a672e8a03?w=300&h=300&fit=crop",
                    "Shampoo 200ml": "https://images.unsplash.com/photo-1556228720-195a672e8a03?w=300&h=300&fit=crop",
                    "Biscuits - 200 g": "https://images.unsplash.com/photo-1555507036-ab1f4038808a?w=300&h=300&fit=crop",
                    "Rice - 5 kg": "https://images.unsplash.com/photo-1586201375761-83865001e31c?w=300&h=300&fit=crop"
                }
                for product in products:
                    if product.name in image_mapping and not product.image_url:
                        product.image_url = image_mapping[product.name]
                db.session.commit()
            # ensure promocode table exists
            if not inspector.has_table('promocode'):
                db.create_all()
        except Exception as e:
            print(f"Startup migration check skipped due to: {e}")
    app.run(debug=True)
