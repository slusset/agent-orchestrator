"""Data models for the TODO API."""

from pydantic import BaseModel


class TodoCreate(BaseModel):
    """Request body for creating a TODO."""
    title: str
    description: str = ""
    completed: bool = False


class TodoUpdate(BaseModel):
    """Request body for updating a TODO."""
    title: str | None = None
    description: str | None = None
    completed: bool | None = None


class Todo(BaseModel):
    """A TODO item with server-assigned ID."""
    id: int
    title: str
    description: str = ""
    completed: bool = False
