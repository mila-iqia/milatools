from rich.console import Console


def _currently_in_a_test() -> bool:
    """Returns True during unit tests (pytest) and False during normal execution."""
    import sys

    return "pytest" in sys.modules


if _currently_in_a_test():
    # Make the console very wide so commands are not wrapped across multiple lines.
    # This makes tests that check the output of commands easier to write.
    console = Console(record=True, width=200, log_time=False, log_path=False)
else:
    console = Console(record=True)
