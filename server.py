import argparse
import hashlib
import os
import tarfile
from concurrent import futures
from pathlib import Path
from typing import Iterable, Optional

from flask import Flask, jsonify, request

app = Flask(__name__)

UPLOAD_FOLDER = 'uploads'
EXTRACT_FOLDER = 'data'
ALLOWED_EXTENSIONS = {'tar.gz'}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(EXTRACT_FOLDER, exist_ok=True)

def allowed_file(filename):
    for ext in ALLOWED_EXTENSIONS:
        if filename.endswith(ext):
            return True
    return False

@app.route('/', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        print("No file sent")
        return jsonify({'error': 'No file sent'}), 400

    file = request.files['file']

    if file.filename == '':
        print("Invalid filename")
        return jsonify({'error': 'No file selected'}), 400

    if file and allowed_file(file.filename):
        filename = file.filename
        filepath = os.path.join(UPLOAD_FOLDER, file.filename)
        file.save(filepath)

        try:
            base_name = os.path.splitext(os.path.splitext(filename)[0])[0]
            extract_path = os.path.join(EXTRACT_FOLDER, base_name)

            os.makedirs(extract_path, exist_ok=True)

            with tarfile.open(filepath, 'r:gz') as tar:
                tar.extractall(path=extract_path)

            return jsonify({
                'success': True,
                'message': f'File {filename} received and extracted to {extract_path}'
            })

        except Exception as e:
            return jsonify({'error': f'Error extracting file: {str(e)}'}), 500

    print("File type not allowed")
    return jsonify({'error': 'File type not allowed'}), 400

@app.route('/check', methods=['GET'])
def checkServer():
    return jsonify({'app': 'Beerus Server', 'version': '1.0'}), 200


def _ensure_grpc_generated(proto_path: Path, out_dir: Path) -> None:
    pb2 = out_dir / "beerus_transfer_pb2.py"
    pb2_grpc = out_dir / "beerus_transfer_pb2_grpc.py"
    if pb2.exists() and pb2_grpc.exists():
        return

    try:
        from grpc_tools import protoc
    except Exception as e:
        raise RuntimeError(
            "grpcio-tools is required to generate stubs. Install requirements.txt in a venv."
        ) from e

    out_dir.mkdir(parents=True, exist_ok=True)
    args = [
        "protoc",
        f"-I{proto_path.parent}",
        f"--python_out={out_dir}",
        f"--grpc_python_out={out_dir}",
        str(proto_path),
    ]
    rc = protoc.main(args)
    if rc != 0:
        raise RuntimeError(f"Failed to generate gRPC stubs (protoc rc={rc})")


def _safe_extract_tar_gz(tar_gz_path: Path, extract_dir: Path) -> None:
    extract_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_gz_path, "r:gz") as tar:
        for member in tar.getmembers():
            member_path = extract_dir / member.name
            resolved = member_path.resolve()
            if not str(resolved).startswith(str(extract_dir.resolve()) + os.sep):
                raise RuntimeError(f"Unsafe path in tar: {member.name}")
        tar.extractall(path=extract_dir)


def _sanitize_filename(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return "artifact.bin"
    name = name.replace("\\", "/").split("/")[-1]
    name = "".join(c if c.isalnum() or c in "._-+" else "_" for c in name)
    return name[:255] if len(name) > 255 else name


def _run_grpc(host: str, port: int) -> None:
    import grpc

    here = Path(__file__).resolve().parent
    proto_path = here / "proto" / "beerus_transfer.proto"
    if not proto_path.exists():
        raise RuntimeError(f"Missing proto file: {proto_path}")

    _ensure_grpc_generated(proto_path=proto_path, out_dir=here)

    import beerus_transfer_pb2 as pb2
    import beerus_transfer_pb2_grpc as pb2_grpc

    APP_NAME = "Beerus gRPC Server"
    APP_VERSION = "1.0"

    class BeerusTransferService(pb2_grpc.BeerusTransferServicer):
        def __init__(self, uploads_root: Path, extracts_root: Path):
            self.uploads_root = uploads_root
            self.extracts_root = extracts_root
            self.uploads_root.mkdir(parents=True, exist_ok=True)
            self.extracts_root.mkdir(parents=True, exist_ok=True)

        def Check(self, request, context):
            return pb2.CheckResponse(app=APP_NAME, version=APP_VERSION)

        def Upload(self, request_iterator: Iterable[pb2.UploadChunk], context):
            meta: Optional[pb2.UploadMetadata] = None
            out_path: Optional[Path] = None
            out_fh = None
            sha256 = hashlib.sha256()
            bytes_received = 0

            try:
                first = True
                expected_seq = 0

                for chunk in request_iterator:
                    if chunk.seq != expected_seq:
                        context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                        context.set_details(f"Bad seq: got {chunk.seq}, expected {expected_seq}")
                        return pb2.UploadResult(success=False, message="Bad sequence number")
                    expected_seq += 1

                    payload = chunk.WhichOneof("payload")
                    if first:
                        first = False
                        if payload != "meta":
                            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                            context.set_details("First chunk must be metadata")
                            return pb2.UploadResult(success=False, message="First chunk must be metadata")
                        meta = chunk.meta

                        device = _sanitize_filename(meta.device_id) if meta.device_id else "unknown-device"
                        pkg = _sanitize_filename(meta.package_name) if meta.package_name else "unknown-package"
                        fname = _sanitize_filename(meta.filename)

                        self.uploads_root.mkdir(parents=True, exist_ok=True)
                        out_path = self.uploads_root / fname
                        out_fh = out_path.open("wb")
                        continue

                    if payload != "data":
                        context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                        context.set_details("Non-first chunks must be data")
                        return pb2.UploadResult(success=False, message="Non-first chunks must be data")

                    data = chunk.data
                    if not data:
                        continue
                    out_fh.write(data)
                    sha256.update(data)
                    bytes_received += len(data)

                if out_fh is None or out_path is None or meta is None:
                    context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                    context.set_details("No metadata/data received")
                    return pb2.UploadResult(success=False, message="No data received")

                out_fh.flush()
                out_fh.close()

                if meta.extract_tar_gz and out_path.name.endswith(".tar.gz"):
                    base = out_path.name[: -len(".tar.gz")]
                    self.extracts_root.mkdir(parents=True, exist_ok=True)
                    extract_dir = self.extracts_root / base
                    _safe_extract_tar_gz(out_path, extract_dir)

                return pb2.UploadResult(
                    success=True,
                    message=f"Saved {out_path}.",
                    saved_path=str(out_path),
                    sha256_hex=sha256.hexdigest(),
                    bytes_received=bytes_received,
                )
            except Exception as e:
                try:
                    if out_fh is not None and not out_fh.closed:
                        out_fh.close()
                except Exception:
                    pass
                context.set_code(grpc.StatusCode.INTERNAL)
                context.set_details(str(e))
                return pb2.UploadResult(success=False, message=str(e))

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
    pb2_grpc.add_BeerusTransferServicer_to_server(
        BeerusTransferService(uploads_root=Path(UPLOAD_FOLDER), extracts_root=Path(EXTRACT_FOLDER)),
        server,
    )
    server.add_insecure_port(f"{host}:{port}")
    server.start()
    print(f"[+] {APP_NAME} listening on {host}:{port}")
    server.wait_for_termination()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Beerus Server (HTTP or gRPC)")
    parser.add_argument("--grpc", action="store_true", help="Run gRPC server instead of HTTP")

    parser.add_argument("--host", default="0.0.0.0", help="Bind host")
    parser.add_argument("--port", type=int, default=6524, help="Bind port")
    args = parser.parse_args()

    if args.grpc:
        _run_grpc(host=args.host, port=args.port)
    else:
        app.run(host=args.host, port=args.port)