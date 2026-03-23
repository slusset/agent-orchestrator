"""Utility functions for the calculator."""

# Bug: this creates a circular import with calculator.py
from calculator import Calculator


def validate_number(value):
    """Ensure value is a number."""
    if not isinstance(value, (int, float)):
        raise TypeError(f"Expected number, got {type(value).__name__}")


def create_calculator():
    """Factory function for Calculator."""
    return Calculator()
