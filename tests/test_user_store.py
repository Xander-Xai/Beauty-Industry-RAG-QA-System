import os
import tempfile
import pytest
from auth.user_store import UserStore, User


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_users.db")
        yield UserStore(db_path=db_path)


def test_create_user(store):
    user = store.create_user("u1", "alice", "password123", "Alice", ["rd"], ["研发部"])
    assert user.user_id == "u1"
    assert user.username == "alice"
    assert user.role_mask == 0x02  # rd
    assert user.dept_mask == 0x01  # 研发部
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
    store.create_user("u6", "frank", "pass", "Frank", ["rd"], ["研发部"])
    updated = store.update_user_roles("u6", ["admin", "rd"], ["法规部"])
    assert updated.role_mask == 0x01 | 0x02  # admin | rd
    assert updated.dept_mask == 0x04  # 法规部
    assert "admin" in updated.roles
    assert "法规部" in updated.departments
