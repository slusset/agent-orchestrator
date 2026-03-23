"""Tests for the hello endpoint."""

from fastapi.testclient import TestClient
from app import app


client = TestClient(app)


def test_hello_returns_200():
    response = client.get("/hello")
    assert response.status_code == 200


def test_hello_returns_message():
    response = client.get("/hello")
    data = response.json()
    assert data["message"] == "hello world"
