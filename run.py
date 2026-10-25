"""Start the local application without a JavaScript build toolchain."""
import argparse
from pathlib import Path
import os

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="MedLabel Review 本地说明书审核工作台")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data-dir", default=str(Path(__file__).parent / "data"))
    args = parser.parse_args()
    os.environ["MEDLABEL_DATA_DIR"] = str(Path(args.data_dir).resolve())
    uvicorn.run("app.main:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
