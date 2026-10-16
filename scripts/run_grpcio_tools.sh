#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
GRPCIO_VERSION="$(poetry run python -c 'import importlib.metadata; print(importlib.metadata.version("grpcio"))')"
poetry run python -m pip install "grpcio-tools==${GRPCIO_VERSION}" mypy-protobuf
poetry run python -m grpc_tools.protoc \
  -I src/meetnote/embeddings/proto --python_out=src/meetnote/embeddings/proto \
  --grpc_python_out=src/meetnote/embeddings/proto --mypy_out=src/meetnote/embeddings/proto \
  --mypy_grpc_out=src/meetnote/embeddings/proto src/meetnote/embeddings/proto/embedding.proto
sed -i 's/^import embedding_pb2 as/from . import embedding_pb2 as/' src/meetnote/embeddings/proto/embedding_pb2_grpc.py
sed -i 's/^import embedding_pb2/from . import embedding_pb2/' src/meetnote/embeddings/proto/embedding_pb2_grpc.pyi
