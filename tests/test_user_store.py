import os
import tempfile
import pytest
from auth.user_store import UserStore, User, ROLES, DEPARTMENTS


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_users.db")
        yield UserStore(db_path=db_path)


def test_create_user(store):
    user = store.create_user("u1", "alice", "password123", "Alice", ["rd"], ["rd_dept"])
    assert user.user_id == "u1"
    assert user.username == "alice"
    assert user.role_mask == ROLES["rd"]  # rd role from config.json
    assert user.dept_mask == DEPARTMENTS["rd_dept"]  # rd_dept from config.json
    assert "rd" in user.roles


def test_authenticate_success(store):
    store.create_user("u2", "bob", "secret", "Bob", ["admin"], [])
    user = store.authenticate("bob", "secret")
    assert user is not None
    assert user.username == "bob"


def test_authenticate_wrong_password(store):
    store.create_user("u3", "carol", "password", "Carol", ["rd"], [])
    user = store.authenticate("carol", "wrongpassword")
    assert user is None


def test_authenticate_nonexistent_user(store):
    user = store.authenticate("nobody", "password")
    assert user is None


def test_list_users(store):
    store.create_user("u4", "dave", "pass1", "Dave", ["rd"], [])
    store.create_user("u5", "eve", "pass2", "Eve", ["sales"], [])
    users = store.list_users()
    assert len(users) == 2


def test_update_roles(store):
    store.create_user("u6", "frank", "pass", "Frank", ["rd"], ["rd_dept"])
    updated = store.update_user_roles("u6", ["admin", "rd"], ["regulation_dept"])
    # admin mask (0x7FFFFFFF) 已包含所有位，OR rd(1) 结果不变
    assert updated.role_mask == ROLES["admin"] | ROLES["rd"]
    assert updated.dept_mask == DEPARTMENTS["regulation_dept"]
    assert "admin" in updated.roles
    assert "regulation_dept" in updated.departments
