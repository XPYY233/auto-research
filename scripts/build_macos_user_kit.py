#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from auto_research.product_release_kit import build_macos_user_kit


def main() -> int:
    parser = argparse.ArgumentParser(description="组装 Auto Research macOS 用户套件")
    parser.add_argument("--file", action="append", required=True)
    parser.add_argument("--output-directory", required=True)
    parser.add_argument("--release-name", required=True)
    arguments = parser.parse_args()
    result = build_macos_user_kit(
        files=tuple(Path(value) for value in arguments.file),
        output_directory=arguments.output_directory,
        release_name=arguments.release_name,
    )
    print(
        json.dumps(
            {
                "directory": str(result.directory),
                "zip_path": str(result.zip_path),
                "zip_sha256": result.zip_sha256,
                "files": [artifact.path.name for artifact in result.artifacts],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
