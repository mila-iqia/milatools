from rich.console import Console


def _currently_in_a_test() -> bool:
    """Returns True during unit tests (pytest) and False during normal execution."""
    import sys

    return "pytest" in sys.modules


if _currently_in_a_test():
    console = Console(record=True, width=1000, log_time=False, log_path=False)
else:
    console = Console(record=True)
