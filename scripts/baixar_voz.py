"""Baixa o modelo de voz do TTS (Fase 11) para TTS_MODELO_DIR.

    uv run python scripts/baixar_voz.py
"""

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

KOKORO = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
PIPER = "https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/"


def arquivos() -> dict[str, str]:
    if config.TTS_MOTOR == "kokoro":
        return {n: KOKORO + n for n in (config.TTS_MODELO, "voices-v1.0.bin")}
    base = f"pt_BR-{config.TTS_VOZ}-medium"
    return {f"{base}{ext}": f"{PIPER}{config.TTS_VOZ}/medium/{base}{ext}" for ext in (".onnx", ".onnx.json")}


def main() -> None:
    pasta = Path(config.TTS_MODELO_DIR)
    pasta.mkdir(parents=True, exist_ok=True)
    for nome, url in arquivos().items():
        destino = pasta / nome
        if destino.exists():
            print(f"já existe: {destino}")
            continue
        print(f"baixando {url}")
        tmp = destino.with_suffix(".parcial")
        with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r, open(tmp, "wb") as f:
            r.raise_for_status()
            for pedaco in r.iter_bytes(1 << 20):
                f.write(pedaco)
        tmp.replace(destino)
        print(f"ok: {destino} ({destino.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
