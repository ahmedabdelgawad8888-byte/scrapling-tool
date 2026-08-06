import shutil
from pathlib import Path

_PROTECTED_PARTS = {".venv", ".git", "instagram_data", "instagram_pages", "instagram_pages_structured"}


def _is_protected(path: Path, base_dir: Path) -> bool:
    try:
        relative = path.resolve().relative_to(base_dir.resolve())
    except ValueError:
        return True
    return any(part in _PROTECTED_PARTS for part in relative.parts)


# Clean up after installing for local development
def clean():
    base_dir = Path.cwd()

    cleanup_patterns = [
        "build",
        "dist",
        "*.egg-info",
        "__pycache__",
        ".cache",
        ".eggs",
        ".pytest_cache",
    ]

    for pattern in cleanup_patterns:
        for path in base_dir.glob(pattern):
            if _is_protected(path, base_dir):
                continue
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                print(f"Removed: {path}")
            except Exception as e:
                print(f"Could not remove {path}: {e}")

    # Nested source/test caches are disposable; never walk into the venv,
    # repository metadata, or collected creator datasets.
    nested_caches = [
        path
        for path in base_dir.rglob("*")
        if path.is_dir()
        and path.name in {"__pycache__", ".pytest_cache"}
        and not _is_protected(path, base_dir)
    ]
    for path in sorted(nested_caches, key=lambda item: len(item.parts), reverse=True):
        try:
            shutil.rmtree(path)
            print(f"Removed nested cache: {path}")
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"Could not remove {path}: {e}")

    for path in base_dir.rglob("*.py[co]"):
        if _is_protected(path, base_dir):
            continue
        try:
            path.unlink()
            print(f"Removed compiled file: {path}")
        except Exception as e:
            print(f"Could not remove {path}: {e}")


if __name__ == "__main__":
    clean()
