"""Commit a stock balance and its audit entry as one transaction."""
from sqlalchemy import update

from models import Product, StockLog

MAX_QUANTITY = 2_147_483_647


class StockConflict(Exception):
    """Another request changed the balance since this request read it."""


def record_stock_change(session, product, user_id, change_type, qty, reason):
    if not isinstance(qty, int) or not 0 <= qty <= MAX_QUANTITY:
        raise ValueError("Quantity must be a nonnegative whole number within the supported range.")
    old_quantity = product.quantity
    if change_type in ("in", "out") and qty == 0:
        raise ValueError("Enter a quantity greater than zero for stock in or out.")
    if change_type == "in":
        new_quantity = old_quantity + qty
    elif change_type == "out":
        new_quantity = old_quantity - qty
    elif change_type == "adjustment":
        new_quantity = qty
    else:
        raise ValueError("Invalid stock change type.")
    if new_quantity < 0:
        raise ValueError("Cannot remove more stock than is currently available.")
    if new_quantity > MAX_QUANTITY:
        raise ValueError("Resulting stock exceeds the supported quantity range.")
    result = session.execute(
        update(Product)
        .where(Product.id == product.id, Product.quantity == old_quantity)
        .values(quantity=new_quantity)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise StockConflict("Stock changed while you were editing. Review the current balance and try again.")
    session.add(StockLog(product_id=product.id, user_id=user_id,
                         change_type=change_type, change_qty=new_quantity - old_quantity,
                         reason=reason))
    session.commit()
