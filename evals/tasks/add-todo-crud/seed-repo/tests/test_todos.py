"""Tests for the TODO CRUD API."""

import pytest
from fastapi.testclient import TestClient
from app import app
import store


@pytest.fixture(autouse=True)
def reset_store():
    """Reset the in-memory store before each test."""
    store.reset()
    yield
    store.reset()


client = TestClient(app)


class TestCreateTodo:
    def test_create_returns_201(self):
        response = client.post("/todos", json={"title": "Buy milk"})
        assert response.status_code == 201

    def test_create_returns_todo_with_id(self):
        response = client.post("/todos", json={"title": "Buy milk"})
        data = response.json()
        assert "id" in data
        assert data["title"] == "Buy milk"
        assert data["completed"] is False

    def test_create_with_description(self):
        response = client.post("/todos", json={
            "title": "Buy milk",
            "description": "2% from the corner store",
        })
        data = response.json()
        assert data["description"] == "2% from the corner store"

    def test_create_increments_id(self):
        r1 = client.post("/todos", json={"title": "First"})
        r2 = client.post("/todos", json={"title": "Second"})
        assert r2.json()["id"] > r1.json()["id"]


class TestListTodos:
    def test_list_empty(self):
        response = client.get("/todos")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_returns_all(self):
        client.post("/todos", json={"title": "A"})
        client.post("/todos", json={"title": "B"})
        response = client.get("/todos")
        assert len(response.json()) == 2


class TestGetTodo:
    def test_get_existing(self):
        create_resp = client.post("/todos", json={"title": "Buy milk"})
        todo_id = create_resp.json()["id"]

        response = client.get(f"/todos/{todo_id}")
        assert response.status_code == 200
        assert response.json()["title"] == "Buy milk"

    def test_get_missing_returns_404(self):
        response = client.get("/todos/999")
        assert response.status_code == 404


class TestUpdateTodo:
    def test_update_title(self):
        create_resp = client.post("/todos", json={"title": "Buy milk"})
        todo_id = create_resp.json()["id"]

        response = client.put(f"/todos/{todo_id}", json={"title": "Buy oat milk"})
        assert response.status_code == 200
        assert response.json()["title"] == "Buy oat milk"

    def test_update_completed(self):
        create_resp = client.post("/todos", json={"title": "Buy milk"})
        todo_id = create_resp.json()["id"]

        response = client.put(f"/todos/{todo_id}", json={"completed": True})
        assert response.status_code == 200
        assert response.json()["completed"] is True

    def test_update_missing_returns_404(self):
        response = client.put("/todos/999", json={"title": "X"})
        assert response.status_code == 404


class TestDeleteTodo:
    def test_delete_existing(self):
        create_resp = client.post("/todos", json={"title": "Buy milk"})
        todo_id = create_resp.json()["id"]

        response = client.delete(f"/todos/{todo_id}")
        assert response.status_code == 204

    def test_delete_removes_from_list(self):
        create_resp = client.post("/todos", json={"title": "Buy milk"})
        todo_id = create_resp.json()["id"]

        client.delete(f"/todos/{todo_id}")
        response = client.get(f"/todos/{todo_id}")
        assert response.status_code == 404

    def test_delete_missing_returns_404(self):
        response = client.delete("/todos/999")
        assert response.status_code == 404
