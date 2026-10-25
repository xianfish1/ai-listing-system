from __future__ import annotations

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from workers.asset_scanner.scanner import scan_asset_root


def main() -> None:
    root = Path("sample_assets")
    result = scan_asset_root(root)
    assert "assets" in result
    print(f"scanner_ok count={result['count']}")


if __name__ == "__main__":
    main()
