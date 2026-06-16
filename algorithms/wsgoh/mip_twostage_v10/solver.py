from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from algorithms.wsgoh.mip_twostage_v10.cli import main as _cli_main
from algorithms.wsgoh.mip_twostage_v10.config import DESCRIPTION
from algorithms.wsgoh.mip_twostage_v10.workflow import algorithm


def main() -> None:
    _cli_main(algorithm)


if __name__ == "__main__":
    main()
