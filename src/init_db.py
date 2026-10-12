""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from memlib import db


def main() -> None:
    parser = argparse.ArgumentParser(description="初始化 / 升级本地记忆数据库。")
    parser.add_argument("--db", type=Path, default=None, help="默认取 config.toml 的 [db].path")
    parser.add_argument("--config", type=Path, default=db.DEFAULT_CONFIG_PATH)
    args = parser.parse_args()

    try:
        config = db.load_config(args.config)
        db_path = args.db or db.resolve_path(config["db"]["path"])
        path = db.ensure_schema(db_path)
    except (OSError, db.SchemaError, KeyError) as error:
        parser.exit(1, f"初始化失败：{error}\n")
    print(f"Database ready: {path} (schema v{db.SCHEMA_VERSION})")


if __name__ == "__main__":
    main()
