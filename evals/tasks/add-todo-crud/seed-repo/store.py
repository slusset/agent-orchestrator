"""In-memory data store for TODOs."""

# Simple in-memory store: {id: Todo dict}
todos: dict[int, dict] = {}

# Auto-incrementing ID counter
_next_id: int = 1


def get_next_id() -> int:
    """Get the next available ID and increment the counter."""
    global _next_id
    current = _next_id
    _next_id += 1
    return current


def reset():
    """Reset the store (for testing)."""
    global _next_id
    todos.clear()
    _next_id = 1
