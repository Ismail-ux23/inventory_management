from datetime import datetime
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, session, flash
from sqlalchemy import func

from extensions import db
from models import User, Category, Supplier, Product, StockLog

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.config["SECRET_KEY"] = "dev-secret-key-change-this"  # change before deploying
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///inventory.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)
    return wrapped


def current_user():
    if "user_id" in session:
        return User.query.get(session["user_id"])
    return None


@app.context_processor
def inject_user():
    return {"current_user": current_user()}


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.route("/")
def home():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            flash("Email and password are required.", "error")
            return render_template("signup.html")

        if User.query.filter_by(email=email).first():
            flash("An account with that email already exists.", "error")
            return render_template("signup.html")

        # First user to sign up becomes admin; everyone else is staff.
        role = "admin" if User.query.count() == 0 else "staff"

        user = User(email=email, role=role)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        flash("Account created. Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            session["user_id"] = user.id
            return redirect(url_for("dashboard"))

        flash("Invalid email or password.", "error")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.route("/dashboard")
@login_required
def dashboard():
    total_products = Product.query.count()
    total_stock_value = db.session.query(
        func.coalesce(func.sum(Product.price * Product.quantity), 0.0)
    ).scalar()
    low_stock_items = Product.query.filter(Product.quantity <= Product.reorder_level).all()
    recent_logs = (
        StockLog.query.order_by(StockLog.timestamp.desc()).limit(10).all()
    )

    return render_template(
        "dashboard.html",
        total_products=total_products,
        total_stock_value=round(total_stock_value, 2),
        low_stock_items=low_stock_items,
        recent_logs=recent_logs,
    )


# ---------------------------------------------------------------------------
# Product CRUD
# ---------------------------------------------------------------------------

@app.route("/products")
@login_required
def products():
    query = Product.query

    search = request.args.get("search", "").strip()
    category_id = request.args.get("category_id", "")
    low_stock_only = request.args.get("low_stock") == "1"

    if search:
        like = f"%{search}%"
        query = query.filter(db.or_(Product.name.ilike(like), Product.sku.ilike(like)))

    if category_id:
        query = query.filter(Product.category_id == int(category_id))

    if low_stock_only:
        query = query.filter(Product.quantity <= Product.reorder_level)

    all_products = query.order_by(Product.name.asc()).all()
    categories = Category.query.order_by(Category.name.asc()).all()

    return render_template(
        "products.html",
        products=all_products,
        categories=categories,
        search=search,
        selected_category=category_id,
        low_stock_only=low_stock_only,
    )


@app.route("/products/add", methods=["GET", "POST"])
@login_required
def add_product():
    categories = Category.query.order_by(Category.name.asc()).all()
    suppliers = Supplier.query.order_by(Supplier.name.asc()).all()

    if request.method == "POST":
        sku = request.form.get("sku", "").strip()
        name = request.form.get("name", "").strip()

        if not sku or not name:
            flash("SKU and name are required.", "error")
            return render_template("product_form.html", categories=categories, suppliers=suppliers)

        if Product.query.filter_by(sku=sku).first():
            flash("A product with that SKU already exists.", "error")
            return render_template("product_form.html", categories=categories, suppliers=suppliers)

        product = Product(
            sku=sku,
            name=name,
            description=request.form.get("description", "").strip(),
            price=float(request.form.get("price") or 0),
            quantity=int(request.form.get("quantity") or 0),
            reorder_level=int(request.form.get("reorder_level") or 5),
            category_id=request.form.get("category_id") or None,
            supplier_id=request.form.get("supplier_id") or None,
        )
        db.session.add(product)
        db.session.commit()

        # If a starting quantity was given, log it as an initial stock-in.
        if product.quantity > 0:
            log = StockLog(
                product_id=product.id,
                user_id=session["user_id"],
                change_type="in",
                change_qty=product.quantity,
                reason="Initial stock",
            )
            db.session.add(log)
            db.session.commit()

        flash(f"Product '{product.name}' added.", "success")
        return redirect(url_for("products"))

    return render_template("product_form.html", categories=categories, suppliers=suppliers)


@app.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
@login_required
def edit_product(product_id):
    product = Product.query.get_or_404(product_id)
    categories = Category.query.order_by(Category.name.asc()).all()
    suppliers = Supplier.query.order_by(Supplier.name.asc()).all()

    if request.method == "POST":
        new_sku = request.form.get("sku", "").strip()
        existing = Product.query.filter_by(sku=new_sku).first()
        if existing and existing.id != product.id:
            flash("Another product already uses that SKU.", "error")
            return render_template("product_form.html", product=product, categories=categories, suppliers=suppliers)

        product.sku = new_sku
        product.name = request.form.get("name", "").strip()
        product.description = request.form.get("description", "").strip()
        product.price = float(request.form.get("price") or 0)
        product.reorder_level = int(request.form.get("reorder_level") or 5)
        product.category_id = request.form.get("category_id") or None
        product.supplier_id = request.form.get("supplier_id") or None
        # NOTE: quantity is intentionally NOT edited here — use the
        # "Adjust Stock" action so every change is logged.

        db.session.commit()
        flash("Product updated.", "success")
        return redirect(url_for("products"))

    return render_template("product_form.html", product=product, categories=categories, suppliers=suppliers)


@app.route("/products/<int:product_id>/delete", methods=["POST"])
@login_required
def delete_product(product_id):
    product = Product.query.get_or_404(product_id)
    db.session.delete(product)
    db.session.commit()
    flash(f"Product '{product.name}' deleted.", "success")
    return redirect(url_for("products"))


# ---------------------------------------------------------------------------
# Stock adjustments
# ---------------------------------------------------------------------------

@app.route("/products/<int:product_id>/stock", methods=["GET", "POST"])
@login_required
def adjust_stock(product_id):
    product = Product.query.get_or_404(product_id)

    if request.method == "POST":
        change_type = request.form.get("change_type")  # in / out / adjustment
        reason = request.form.get("reason", "").strip()
        try:
            qty = int(request.form.get("qty") or 0)
        except ValueError:
            qty = 0

        if qty <= 0:
            flash("Enter a quantity greater than zero.", "error")
            return render_template("stock_log.html", product=product)

        if change_type == "in":
            delta = qty
        elif change_type == "out":
            if qty > product.quantity:
                flash("Cannot remove more stock than is currently available.", "error")
                return render_template("stock_log.html", product=product)
            delta = -qty
        elif change_type == "adjustment":
            # Manual correction: set absolute quantity instead of delta.
            delta = qty - product.quantity
        else:
            flash("Invalid stock change type.", "error")
            return render_template("stock_log.html", product=product)

        product.quantity += delta
        log = StockLog(
            product_id=product.id,
            user_id=session["user_id"],
            change_type=change_type,
            change_qty=delta,
            reason=reason,
        )
        db.session.add(log)
        db.session.commit()

        flash("Stock updated.", "success")
        return redirect(url_for("products"))

    logs = (
        StockLog.query.filter_by(product_id=product.id)
        .order_by(StockLog.timestamp.desc())
        .all()
    )
    return render_template("stock_log.html", product=product, logs=logs)


# ---------------------------------------------------------------------------
# Categories & Suppliers (lightweight management)
# ---------------------------------------------------------------------------

@app.route("/categories", methods=["GET", "POST"])
@login_required
def categories():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if name and not Category.query.filter_by(name=name).first():
            db.session.add(Category(name=name))
            db.session.commit()
            flash("Category added.", "success")
        else:
            flash("Category name is required and must be unique.", "error")
        return redirect(url_for("categories"))

    all_categories = Category.query.order_by(Category.name.asc()).all()
    return render_template("categories.html", categories=all_categories)


@app.route("/categories/<int:category_id>/delete", methods=["POST"])
@login_required
def delete_category(category_id):
    category = Category.query.get_or_404(category_id)
    db.session.delete(category)
    db.session.commit()
    flash("Category deleted.", "success")
    return redirect(url_for("categories"))


@app.route("/suppliers", methods=["GET", "POST"])
@login_required
def suppliers():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        contact_info = request.form.get("contact_info", "").strip()
        if name:
            db.session.add(Supplier(name=name, contact_info=contact_info))
            db.session.commit()
            flash("Supplier added.", "success")
        else:
            flash("Supplier name is required.", "error")
        return redirect(url_for("suppliers"))

    all_suppliers = Supplier.query.order_by(Supplier.name.asc()).all()
    return render_template("suppliers.html", suppliers=all_suppliers)


@app.route("/suppliers/<int:supplier_id>/delete", methods=["POST"])
@login_required
def delete_supplier(supplier_id):
    supplier = Supplier.query.get_or_404(supplier_id)
    db.session.delete(supplier)
    db.session.commit()
    flash("Supplier deleted.", "success")
    return redirect(url_for("suppliers"))


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

@app.route("/reports")
@login_required
def reports():
    products_by_value = Product.query.all()
    products_by_value.sort(key=lambda p: p.stock_value, reverse=True)

    total_value = sum(p.stock_value for p in products_by_value)

    # Most-moved products (by absolute total quantity change)
    movement = (
        db.session.query(
            StockLog.product_id,
            func.sum(func.abs(StockLog.change_qty)).label("total_movement"),
        )
        .group_by(StockLog.product_id)
        .order_by(func.sum(func.abs(StockLog.change_qty)).desc())
        .limit(10)
        .all()
    )
    movement_data = []
    for product_id, total in movement:
        product = Product.query.get(product_id)
        if product:
            movement_data.append((product, total))

    return render_template(
        "reports.html",
        products_by_value=products_by_value,
        total_value=round(total_value, 2),
        movement_data=movement_data,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True, port=5005)
