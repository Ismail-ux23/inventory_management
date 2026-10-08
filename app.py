import math
import os
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, session, flash
from sqlalchemy import func

from extensions import db
from models import User, Category, Supplier, Product, StockLog
from stock import record_stock_change, StockConflict, MAX_QUANTITY

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.config["SECRET_KEY"] = "dev-secret-key-change-this"  # change before deploying
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///inventory.db")
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


def nonnegative_integer(value, label, default=0):
    try:
        result = int(value if value not in (None, "") else default)
    except (ValueError, TypeError):
        raise ValueError(f"{label} must be a whole number.") from None
    if not 0 <= result <= MAX_QUANTITY:
        raise ValueError(f"{label} must be between 0 and {MAX_QUANTITY}.")
    return result


def product_fields(form, *, include_quantity=False):
    sku = form.get("sku", "").strip()
    name = form.get("name", "").strip()
    if not sku or not name:
        raise ValueError("SKU and name are required.")
    if len(sku) > 50 or len(name) > 200:
        raise ValueError("SKU must be at most 50 characters and name at most 200.")
    try:
        price = float(form.get("price") or 0)
    except (ValueError, TypeError):
        raise ValueError("Price must be a valid number.") from None
    if not math.isfinite(price) or price < 0:
        raise ValueError("Price must be finite and nonnegative.")
    fields = dict(sku=sku, name=name, price=price,
                  description=form.get("description", "").strip(),
                  reorder_level=nonnegative_integer(form.get("reorder_level"), "Reorder level", 5))
    for key, model in (("category_id", Category), ("supplier_id", Supplier)):
        value = form.get(key)
        identifier = nonnegative_integer(value, key) if value else None
        if identifier is not None and db.session.get(model, identifier) is None:
            raise ValueError("Select an existing category or supplier.")
        fields[key] = identifier
    if include_quantity:
        fields["quantity"] = nonnegative_integer(form.get("quantity"), "Starting quantity")
    return fields


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
        try:
            category_number = nonnegative_integer(category_id, "Category")
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("products"))
        query = query.filter(Product.category_id == category_number)

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
        try:
            fields = product_fields(request.form, include_quantity=True)
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("product_form.html", categories=categories, suppliers=suppliers), 400
        if Product.query.filter_by(sku=fields["sku"]).first():
            flash("A product with that SKU already exists.", "error")
            return render_template("product_form.html", categories=categories, suppliers=suppliers), 400

        product = Product(**fields)
        db.session.add(product)
        db.session.flush()
        if product.quantity > 0:
            db.session.add(StockLog(product_id=product.id, user_id=session["user_id"],
                                    change_type="in", change_qty=product.quantity, reason="Initial stock"))
        # The product and initial audit entry succeed or roll back together.
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
        try:
            fields = product_fields(request.form)
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("product_form.html", product=product, categories=categories, suppliers=suppliers), 400
        existing = Product.query.filter_by(sku=fields["sku"]).first()
        if existing and existing.id != product.id:
            flash("Another product already uses that SKU.", "error")
            return render_template("product_form.html", product=product, categories=categories, suppliers=suppliers), 400
        for key, value in fields.items():
            setattr(product, key, value)
        # Quantity changes only through the audited stock action.

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
        try:
            if not request.form.get("qty", "").strip():
                raise ValueError("Quantity is required.")
            qty = nonnegative_integer(request.form.get("qty"), "Quantity")
            record_stock_change(db.session, product, session["user_id"],
                                request.form.get("change_type"), qty,
                                request.form.get("reason", "").strip())
        except (ValueError, StockConflict) as exc:
            db.session.rollback()
            flash(str(exc), "error")
            logs = StockLog.query.filter_by(product_id=product.id).order_by(StockLog.timestamp.desc()).all()
            return render_template("stock_log.html", product=product, logs=logs), 409 if isinstance(exc, StockConflict) else 400

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
