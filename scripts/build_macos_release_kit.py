#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from auto_research.product_release_kit import build_macos_release_kit


def main() -> int:
    parser = argparse.ArgumentParser(description="组装 macOS App 与官方资料包发布套件")
    parser.add_argument("--dmg", required=True)
    parser.add_argument("--package", required=True)
    parser.add_argument("--quickstart", required=True)
    parser.add_argument("--output-directory", required=True)
    parser.add_argument("--release-name", required=True)
    arguments = parser.parse_args()
    result = build_macos_release_kit(
        dmg_path=arguments.dmg,
        official_package_path=arguments.package,
        quickstart_path=arguments.quickstart,
        output_directory=arguments.output_directory,
        release_name=arguments.release_name,
    )
    print(
        json.dumps(
            {
                "directory": str(result.directory),
                "zip_path": str(result.zip_path),
                "zip_sha256": result.zip_sha256,
                "artifacts": [
                    {
                        "name": artifact.path.name,
                        "sha256": artifact.sha256,
                        "size_bytes": artifact.size_bytes,
                    }
                    for artifact in result.artifacts
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
