# PyInstaller spec: frozen noosphere-core sidecar for the packaged .app.
# Build (from repo root; torch/sentence-transformers deliberately absent):
#   uv run --exact --no-dev --extra onnx --extra websearch --group pkg \
#     pyinstaller --noconfirm --distpath app/src-tauri/sidecar packaging/noosphere-core.spec
#
# Onedir (not onefile): Tauri ships the folder under Contents/Resources and the
# shell execs the inner binary — no per-launch self-extraction, and every dylib
# is a real file that codesign can sign for notarization.

from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []
# The vendored ideonomy catalog (repo-root data, not a Python package) —
# server._vendor_ideonomy() finds it under sys._MEIPASS when frozen (#26).
datas += [("../vendor/ideonomy", "vendor/ideonomy")]
# In-app gateway creation (issue #28): aws_setup.gateway_template() reads this
# under sys._MEIPASS/infra when frozen.
datas += [("gateway.yaml", "infra")]
# Native-extension packages whose libs/data PyInstaller's static scan misses.
for pkg in ("ladybug", "igraph", "onnxruntime", "tokenizers", "duckdb"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

hiddenimports += [
    "keyring.backends.macOS",  # selected at runtime by entry points
]

a = Analysis(
    ["frozen_entry.py"],
    pathex=["../src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=[
        # The torch stack must never ride along (ticket #22 owner constraint:
        # slim sealed app; ONNX is the only inference path when frozen).
        "torch",
        "sentence_transformers",
        "transformers",
        "tkinter",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="noosphere-core",
    console=True,
    target_arch="arm64",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="noosphere-core",
)
