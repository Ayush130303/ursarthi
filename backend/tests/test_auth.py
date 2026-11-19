from app.api.routes.auth import REFRESH_COOKIE_NAME
from tests.conftest import make_user


def test_register_login_me(client):
    h = make_user(client)
    me = client.get("/auth/me", headers=h).json()
    assert me["name"] == "Pema Bhutia" and me["has_preferences"] is False
    assert client.post("/auth/login", json={"email": me["email"], "password": "wrong-pass"}).status_code == 401
    assert client.post("/auth/login", json={"email": me["email"], "password": "TestPass123!"}).status_code == 200


def test_protected_routes_need_token(client):
    client.cookies.clear()
    assert client.get("/trips").status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_refresh_rotation_and_logout(client):
    make_user(client)
    old = client.cookies.get(REFRESH_COOKIE_NAME)
    assert client.post("/auth/refresh").status_code == 200
    assert client.cookies.get(REFRESH_COOKIE_NAME) != old
    client.cookies.set(REFRESH_COOKIE_NAME, old, path="/auth")
    assert client.post("/auth/refresh").status_code == 401   # old token can't be reused
    make_user(client)
    token = client.cookies.get(REFRESH_COOKIE_NAME)
    client.post("/auth/logout")
    client.cookies.set(REFRESH_COOKIE_NAME, token, path="/auth")
    assert client.post("/auth/refresh").status_code == 401


def test_password_reset(client, monkeypatch):
    h = make_user(client)
    email = client.get("/auth/me", headers=h).json()["email"]
    got = {}
    monkeypatch.setattr("app.api.routes.auth.send_password_reset_email", lambda to, tok: got.setdefault("t", tok))
    client.post("/auth/forgot-password", json={"email": email})
    assert client.post("/auth/reset-password", json={"token": got["t"], "new_password": "NewPass123!"}).status_code == 200
    assert client.post("/auth/reset-password", json={"token": got["t"], "new_password": "Again123!!"}).status_code == 400
    assert client.post("/auth/login", json={"email": email, "password": "NewPass123!"}).status_code == 200
