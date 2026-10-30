def normalize(value: int) -> int:
    """Reject negative prices."""
    return max(value, 0)


def total(value: int) -> int:
    return normalize(value)
