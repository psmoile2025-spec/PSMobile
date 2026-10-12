import os
import uuid
from datetime import datetime
from functools import wraps
from io import BytesIO

import pandas as pd
from dotenv import load_dotenv
from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, send_file, session, abort
)
from flask_wtf import FlaskForm, CSRFProtect
from flask_wtf.file import FileField, FileAllowed
from wtforms import StringField, IntegerField, SelectField, TextAreaField, PasswordField
from wtforms.validators import DataRequired, Length, NumberRange, Optional
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from supabase import create_client, Client

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "dev-secret-change-me")
csrf = CSRFProtect(app)

# Supabase
url: str = os.environ.get("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(url, key)

# Auth config (simple single-admin for now)
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD_HASH = generate_password_hash(
    os.getenv("ADMIN_PASSWORD", "admin123")
)

# ---------- Forms ----------
class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(max=80)])
    password = PasswordField("Password", validators=[DataRequired()])

class ProductForm(FlaskForm):
    product_code = StringField("Product Code", validators=[DataRequired(), Length(max=50)])
    name = StringField("Name", validators=[DataRequired(), Length(max=200)])
    brand = StringField("Brand", validators=[Optional(), Length(max=100)])
    model = StringField("Model", validators=[Optional(), Length(max=100)])
    category = StringField("Category", validators=[Optional(), Length(max=100)])
    initial_stock = IntegerField("Initial Stock", validators=[Optional(), NumberRange(min=0)], default=0)
    image = FileField("Image", validators=[
        FileAllowed(["jpg", "jpeg", "png", "webp", "gif"], "Images only!")
    ])

class StockMoveForm(FlaskForm):
    product_id = SelectField("Product", validators=[DataRequired()], coerce=str)
    type = SelectField("Type", choices=[("IN", "Stock In"), ("OUT", "Stock Out")], validators=[DataRequired()])
    quantity = IntegerField("Quantity", validators=[DataRequired(), NumberRange(min=1)])
    reason = TextAreaField("Reason", validators=[Optional(), Length(max=500)])

# ---------- Auth Helpers ----------
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("logged_in"):
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login", next=request.url))
        return f(*args, **kwargs)
    return decorated_function

# ---------- Data Helpers ----------
def get_products(search: str | None = None, archived: bool = False, page: int = 1, per_page: int = 50):
    query = supabase.table("products").select("*").eq("is_archived", archived).order("name")
    if search:
        query = query.or_(
            f"name.ilike.%{search}%,product_code.ilike.%{search}%,"
            f"brand.ilike.%{search}%,model.ilike.%{search}%"
        )
    # Simple offset pagination
    start = (page - 1) * per_page
    end = start + per_page - 1
    result = query.range(start, end).execute()
    return result.data or []

def get_product(product_id: str):
    try:
        result = supabase.table("products").select("*").eq("id", product_id).single().execute()
        return result.data
    except Exception:
        return None

def get_stock_history(product_id: str | None = None, start_date: str | None = None,
                      end_date: str | None = None, limit: int = 200):
    query = (
        supabase.table("stock_movements")
        .select("*, products(name, product_code)")
        .order("created_at", desc=True)
        .limit(limit)
    )
    if product_id:
        query = query.eq("product_id", product_id)
    if start_date:
        query = query.gte("created_at", start_date)
    if end_date:
        query = query.lte("created_at", end_date + "T23:59:59")
    result = query.execute()
    return result.data or []

def upload_image(file) -> str | None:
    """Validate and upload image. Returns public URL or None."""
    if not file or file.filename == "":
        return None

    # Basic validation
    if not file.content_type or not file.content_type.startswith("image/"):
        raise ValueError("Invalid file type. Only images are allowed.")

    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(0)
    if size > 5 * 1024 * 1024:  # 5 MB
        raise ValueError("Image too large (max 5 MB).")

    ext = os.path.splitext(secure_filename(file.filename))[1].lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        raise ValueError("Unsupported image format.")

    file_path = f"products/{uuid.uuid4().hex}{ext}"
    file_bytes = file.read()

    supabase.storage.from_("product-images").upload(
        file_path,
        file_bytes,
        file_options={"content-type": file.content_type}
    )
    return supabase.storage.from_("product-images").get_public_url(file_path)

# ---------- Routes ----------
@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("logged_in"):
        return redirect(url_for("index"))

    form = LoginForm()
    if form.validate_on_submit():
        if (form.username.data == ADMIN_USERNAME and
                check_password_hash(ADMIN_PASSWORD_HASH, form.password.data)):
            session["logged_in"] = True
            session["username"] = form.username.data
            flash("Logged in successfully.", "success")
            next_url = request.args.get("next") or url_for("index")
            return redirect(next_url)
        flash("Invalid username or password.", "danger")
    return render_template("login.html", form=form)

@app.route("/logout")
@login_required
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))

@app.route("/")
@login_required
def index():
    all_prods = supabase.table("products").select("current_stock").eq("is_archived", False).execute().data or []
    total_products = len(all_prods)
    total_units = sum(p.get("current_stock", 0) for p in all_prods)
    low_stock = len([p for p in all_prods if p.get("current_stock", 0) <= 5])

    recent_activity = get_stock_history(limit=10)

    return render_template(
        "index.html",
        total_products=total_products,
        total_units=total_units,
        low_stock=low_stock,
        recent_activity=recent_activity,
    )

@app.route("/products")
@login_required
def products():
    search = request.args.get("search", "").strip() or None
    page = request.args.get("page", 1, type=int)
    prods = get_products(search=search, page=page)
    return render_template("products.html", products=prods, search=search or "", page=page)

@app.route("/product/add", methods=["GET", "POST"])
@login_required
def product_add():
    form = ProductForm()
    if form.validate_on_submit():
        try:
            image_url = upload_image(form.image.data) if form.image.data else None

            data = {
                "product_code": form.product_code.data.strip(),
                "name": form.name.data.strip(),
                "brand": form.brand.data.strip() if form.brand.data else None,
                "model": form.model.data.strip() if form.model.data else None,
                "category": form.category.data.strip() if form.category.data else None,
                "image_url": image_url,
                "current_stock": form.initial_stock.data or 0,
            }

            supabase.table("products").insert(data).execute()
            flash("Product added successfully!", "success")
            return redirect(url_for("products"))
        except Exception as e:
            flash(f"Error adding product: {str(e)}", "danger")

    return render_template("product_form.html", form=form, action="Add")

@app.route("/product/edit/<id>", methods=["GET", "POST"])
@login_required
def product_edit(id):
    prod = get_product(id)
    if not prod:
        abort(404)

    form = ProductForm(data=prod)
    # Don't require initial_stock on edit
    form.initial_stock.validators = [Optional(), NumberRange(min=0)]

    if form.validate_on_submit():
        try:
            image_url = prod.get("image_url")
            if form.image.data:
                image_url = upload_image(form.image.data)

            data = {
                "product_code": form.product_code.data.strip(),
                "name": form.name.data.strip(),
                "brand": form.brand.data.strip() if form.brand.data else None,
                "model": form.model.data.strip() if form.model.data else None,
                "category": form.category.data.strip() if form.category.data else None,
                "image_url": image_url,
                "updated_at": datetime.utcnow().isoformat(),
            }

            supabase.table("products").update(data).eq("id", id).execute()
            flash("Product updated!", "success")
            return redirect(url_for("products"))
        except Exception as e:
            flash(f"Error updating product: {str(e)}", "danger")

    return render_template("product_form.html", form=form, product=prod, action="Edit")

@app.route("/product/archive/<id>", methods=["POST"])
@login_required
def product_archive(id):
    try:
        supabase.table("products").update({"is_archived": True}).eq("id", id).execute()
        flash("Product archived.", "info")
    except Exception as e:
        flash(f"Error: {str(e)}", "danger")
    return redirect(url_for("products"))

@app.route("/stock/move", methods=["GET", "POST"])
@login_required
def stock_move():
    form = StockMoveForm()
    products = get_products(archived=False)
    form.product_id.choices = [
        (p["id"], f"{p['product_code']} — {p['name']} (Stock: {p['current_stock']})")
        for p in products
    ]

    if form.validate_on_submit():
        product_id = form.product_id.data
        move_type = form.type.data
        qty = form.quantity.data
        reason = form.reason.data.strip() if form.reason.data else None

        try:
            prod = get_product(product_id)
            if not prod:
                flash("Product not found.", "danger")
                return redirect(url_for("stock_move"))

            current_qty = prod["current_stock"]

            if move_type == "OUT" and current_qty < qty:
                flash("Insufficient stock available!", "danger")
                return redirect(url_for("stock_move"))

            new_qty = current_qty + qty if move_type == "IN" else current_qty - qty

            # Conditional update to reduce race conditions
            update_query = (
                supabase.table("products")
                .update({"current_stock": new_qty, "updated_at": datetime.utcnow().isoformat()})
                .eq("id", product_id)
            )
            if move_type == "OUT":
                update_query = update_query.gte("current_stock", qty)

            result = update_query.execute()
            if not result.data:
                flash("Stock update failed (possibly concurrent change). Please try again.", "danger")
                return redirect(url_for("stock_move"))

            # Record movement
            move_data = {
                "product_id": product_id,
                "quantity": qty if move_type == "IN" else -qty,
                "type": move_type,
                "reason": reason,
            }
            supabase.table("stock_movements").insert(move_data).execute()

            flash(f"Stock {move_type} recorded successfully!", "success")
            return redirect(url_for("index"))
        except Exception as e:
            flash(f"Error recording movement: {str(e)}", "danger")

    return render_template("stock_movement.html", form=form)

@app.route("/history")
@login_required
def history():
    product_id = request.args.get("product_id") or None
    start_date = request.args.get("start_date") or None
    end_date = request.args.get("end_date") or None

    history_data = get_stock_history(product_id, start_date, end_date)
    products_for_filter = get_products(archived=False)

    return render_template(
        "history.html",
        history=history_data,
        products_for_filter=products_for_filter,
        product_id=product_id,
        start_date=start_date,
        end_date=end_date,
    )

@app.route("/export/<type>")
@login_required
def export_csv(type):
    if type == "inventory":
        data = get_products(archived=False)
        df = pd.DataFrame(data)
        filename = "inventory.csv"
    else:
        data = get_stock_history(limit=5000)
        flat_data = []
        for item in data:
            p = item.get("products") or {}
            flat_data.append({
                "date": item.get("created_at"),
                "code": p.get("product_code"),
                "name": p.get("name"),
                "qty": item.get("quantity"),
                "type": item.get("type"),
                "reason": item.get("reason"),
            })
        df = pd.DataFrame(flat_data)
        filename = "stock_history.csv"

    output = BytesIO()
    df.to_csv(output, index=False)
    output.seek(0)
    return send_file(output, mimetype="text/csv", as_attachment=True, download_name=filename)

# ---------- Error Handlers ----------
@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404

@app.errorhandler(500)
def server_error(e):
    return render_template("500.html"), 500

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)