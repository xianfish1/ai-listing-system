from __future__ import annotations

import argparse
import json
from pathlib import Path

from workers.asset_scanner.scanner import scan_asset_root


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan NAS assets and export an asset index.")
    parser.add_argument("--root", required=True, help="NAS asset root path")
    parser.add_argument("--out", default="data/asset-scan.json", help="Output JSON path")
    parser.add_argument("--category", default="", help="Optional category override")
    parser.add_argument("--max-files", type=int, default=3000, help="Maximum asset files to scan")
    parser.add_argument("--mode", default="auto", choices=["auto", "groups", "recursive"], help="Scan mode")
    args = parser.parse_args()

    result = scan_asset_root(args.root, category_override=args.category, max_files=args.max_files, mode=args.mode)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"Scanned {result['count']} assets from {result['root']}")
    if result["errors"]:
        print(f"Errors: {len(result['errors'])}")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
