from __future__ import annotations

import shutil
from pathlib import Path


def main() -> int:
    outputs_dir = Path(__file__).resolve().parent
    preserved_names = {"clean.py", "leaderboard", "lower_bounds"}

    removed_paths: list[Path] = []
    skipped_paths: list[Path] = []

    for path in sorted(outputs_dir.iterdir(), key=lambda candidate: candidate.name):
        if path.name in preserved_names:
            skipped_paths.append(path)
            continue

        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        removed_paths.append(path)

    print(f"Cleaned outputs directory: {outputs_dir}")
    if removed_paths:
        print("Removed:")
        for path in removed_paths:
            print(f"  - {path.name}")
    else:
        print("Removed: nothing")

    print("Preserved:")
    for path in skipped_paths:
        print(f"  - {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
