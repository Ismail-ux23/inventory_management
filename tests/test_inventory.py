import importlib
import sys

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session


@pytest.fixture
def inventory(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///' + str(tmp_path / 'test.db'))
    sys.modules.pop('app', None)
    module = importlib.import_module('app')
    module.app.config.update(TESTING=True)
    with module.app.app_context():
        module.db.create_all()
        user = module.User(email='tester@example.com', password_hash='test-only', role='admin')
        module.db.session.add(user)
        module.db.session.commit()
        client = module.app.test_client()
        with client.session_transaction() as session:
            session['user_id'] = user.id
        yield module, client
        module.db.session.remove()
        module.db.drop_all()


def product_data(**changes):
    data = dict(sku='TEST', name='Test product', price='2.50', quantity='5', reorder_level='0')
    data.update(changes)
    return data


@pytest.mark.parametrize('field,value', [
    ('price', '-1'), ('price', 'nan'), ('price', 'inf'), ('price', 'abc'),
    ('quantity', '-1'), ('quantity', '1.5'), ('quantity', '2147483648'),
    ('reorder_level', '-1'), ('reorder_level', 'abc'),
    ('category_id', 'abc'), ('category_id', '999'), ('supplier_id', '999'),
    ('sku', ' '), ('name', ' '),
])
def test_invalid_product_never_persists(inventory, field, value):
    module, client = inventory
    assert client.post('/products/add', data=product_data(**{field: value})).status_code == 400
    assert module.Product.query.count() == module.StockLog.query.count() == 0


def test_initial_quantity_and_zero_reorder(inventory):
    module, client = inventory
    assert client.post('/products/add', data=product_data()).status_code == 302
    product = module.Product.query.one()
    assert (product.quantity, product.reorder_level) == (5, 0)
    assert module.StockLog.query.one().change_qty == 5


@pytest.mark.parametrize('change_type,qty', [('out', '6'), ('in', '0'), ('out', '0'),
    ('adjustment', '-1'), ('adjustment', ''), ('adjustment', ' '),
    ('in', 'abc'), ('in', '1.5'), ('bogus', '1'), ('in', '2147483647')])
def test_invalid_stock_keeps_balance_and_history(inventory, change_type, qty):
    module, client = inventory
    client.post('/products/add', data=product_data())
    product = module.Product.query.one()
    assert client.post(f'/products/{product.id}/stock', data=dict(change_type=change_type, qty=qty)).status_code == 400
    module.db.session.expire_all()
    assert product.quantity == 5
    assert module.StockLog.query.count() == 1


def test_stock_operations_and_zero_correction_reconcile(inventory):
    module, client = inventory
    client.post('/products/add', data=product_data())
    product = module.Product.query.one()
    for change_type, qty in [('in', '3'), ('out', '2'), ('adjustment', '0')]:
        assert client.post(f'/products/{product.id}/stock', data=dict(change_type=change_type, qty=qty)).status_code == 302
    module.db.session.expire_all()
    assert product.quantity == 0
    assert sum(log.change_qty for log in module.StockLog.query.all()) == 0


def test_edit_validation_and_quantity_cannot_be_overwritten(inventory):
    module, client = inventory
    client.post('/products/add', data=product_data())
    product = module.Product.query.one()
    assert client.post(f'/products/{product.id}/edit', data=product_data(price='-1')).status_code == 400
    assert client.post(f'/products/{product.id}/edit', data=product_data(name='Updated', quantity='900')).status_code == 302
    module.db.session.expire_all()
    assert (product.name, product.quantity, product.price) == ('Updated', 5, 2.5)
    assert module.StockLog.query.count() == 1


@pytest.mark.parametrize('change_type,qty', [('in', 3), ('out', 4), ('adjustment', 0)])
def test_stale_writer_cannot_overwrite_committed_balance(inventory, change_type, qty):
    from stock import record_stock_change, StockConflict
    module, client = inventory
    client.post('/products/add', data=product_data())
    identifier = module.Product.query.one().id
    user_id = module.User.query.one().id
    # Two sessions see 5; the first commits 2. The stale second must not
    # overwrite it, oversell it, or record a change that never happened.
    with Session(module.db.engine, expire_on_commit=False) as first, Session(module.db.engine) as second:
        current = first.get(module.Product, identifier)
        stale = second.get(module.Product, identifier)
        # End the read transaction but retain its stale ORM snapshot.
        second.commit()
        stale = second.get(module.Product, identifier)
        second.expunge(stale)
        second.rollback()
        record_stock_change(first, current, user_id, 'out', 3, 'first writer')
        with pytest.raises(StockConflict):
            record_stock_change(second, stale, user_id, change_type, qty, 'stale writer')
        second.rollback()
    module.db.session.expire_all()
    assert module.db.session.get(module.Product, identifier).quantity == 2
    assert [log.change_qty for log in module.StockLog.query.order_by(module.StockLog.id)] == [5, -3]


@pytest.mark.parametrize('initial', [True, False])
def test_failed_audit_insert_rolls_back_balance(inventory, initial):
    module, client = inventory
    if not initial:
        client.post('/products/add', data=product_data())
    def reject_log(mapper, connection, target):
        raise RuntimeError('simulated audit storage failure')
    event.listen(module.StockLog, 'before_insert', reject_log)
    try:
        with pytest.raises(RuntimeError, match='audit storage failure'):
            if initial:
                client.post('/products/add', data=product_data())
            else:
                product = module.Product.query.one()
                client.post(f'/products/{product.id}/stock', data=dict(change_type='in', qty='2'))
        module.db.session.rollback()
    finally:
        event.remove(module.StockLog, 'before_insert', reject_log)
    if initial:
        assert module.Product.query.count() == module.StockLog.query.count() == 0
    else:
        assert module.Product.query.one().quantity == 5
        assert module.StockLog.query.count() == 1


def test_malformed_category_filter_does_not_crash(inventory):
    _, client = inventory
    assert client.get('/products?category_id=abc').status_code == 302


def test_stock_conflict_response_preserves_history(inventory, monkeypatch):
    from stock import StockConflict
    module, client = inventory
    client.post('/products/add', data=product_data())
    product = module.Product.query.one()
    def conflict(*args):
        raise StockConflict('Stock changed while you were editing.')
    monkeypatch.setattr(module, 'record_stock_change', conflict)
    response = client.post(f'/products/{product.id}/stock', data=dict(change_type='in', qty='2'))
    assert response.status_code == 409
    assert b'Initial stock' in response.data
    assert product.quantity == 5
    assert module.StockLog.query.count() == 1
