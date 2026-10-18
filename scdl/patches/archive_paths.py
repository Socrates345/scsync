from pathlib import Path

# scdl/patches/archive_paths.py -> scdl/patches -> scdl/ (package) -> repo root
ROOT = Path(__file__).resolve().parent.parent.parent


def to_relative(path) -> str:
    p = Path(path).resolve()
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def to_absolute(field: str) -> Path:
    p = Path(field)
    return p if p.is_absolute() else (ROOT / p).resolve()
