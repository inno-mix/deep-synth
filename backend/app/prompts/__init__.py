from functools import cache
from pathlib import Path

_DIR = Path(__file__).parent


@cache
def _raw(name: str) -> str:
    return (_DIR / f"{name}.md").read_text()


def prompt(name: str, **kwargs: object) -> str:
    text = _raw(name)
    return text.format(**kwargs) if kwargs else text
