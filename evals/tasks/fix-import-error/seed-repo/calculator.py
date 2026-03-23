"""Calculator module with basic operations."""

# Bug: circular import — utils imports Calculator, Calculator imports utils
from utils import validate_number


class Calculator:
    """Simple calculator with validated inputs."""

    def add(self, a, b):
        validate_number(a)
        validate_number(b)
        return a + b

    def multiply(self, a, b):
        validate_number(a)
        validate_number(b)
        return a * b
