"""Lossless file preparation. Never downsample images or change the document format."""

import copy
import io
import re
import sys
import zipfile
from pathlib import Path, PurePosixPath

MAX_INPUT = 20_000_000
MAX_EXPANDED = 100 * 1024 * 1024
MAX_ENTRIES = 1000


def optimize(name: str, data: bytes) -> tuple[bytes, str]:
    if not data or len(data) > MAX_INPUT:
        return data, "limited"
    try:
        if name.lower().endswith(".pdf"):
            candidate, reason = optimize_pdf(data)
        elif name.lower().endswith(".zip"):
            candidate, reason = optimize_zip(data)
        else:
            return data, "unsupported"
        # Prefer the original unless the saving is meaningful; never return a bigger file.
        if len(candidate) <= len(data) - max(1024, len(data) // 100):
            return candidate, "improved"
        return data, reason if reason != "improved" else "unchanged"
    except Exception:
        # Parser diagnostics can contain private filenames/content. Do not log them.
        return data, "invalid"


def optimize_pdf(data: bytes) -> tuple[bytes, str]:
    import pikepdf

    if b"/ByteRange" in data:
        return data, "protected"
    with pikepdf.Pdf.open(io.BytesIO(data), attempt_recovery=False) as pdf:
        if pdf.is_encrypted or pdf.Root.get("/AcroForm"):
            return data, "protected"
        for obj in pdf.objects:
            if isinstance(obj, pikepdf.Dictionary) and (
                obj.get("/Type") == pikepdf.Name.Sig
                or obj.get("/FT") == pikepdf.Name.Sig
                or "/ByteRange" in obj
            ):
                return data, "protected"
        output = io.BytesIO()
        pdf.save(
            output,
            compress_streams=True,
            recompress_flate=True,
            object_stream_mode=pikepdf.ObjectStreamMode.generate,
            normalize_content=False,
            preserve_pdfa=True,
        )
        candidate = output.getvalue()
        with pikepdf.Pdf.open(io.BytesIO(candidate), attempt_recovery=False) as check:
            if len(check.pages) != len(pdf.pages):
                return data, "invalid"
        return candidate, "improved"


def optimize_zip(data: bytes) -> tuple[bytes, str]:
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        entries = source.infolist()
        names = [entry.filename for entry in entries]
        if (
            len(entries) > MAX_ENTRIES
            or len(set(names)) != len(names)
            or sum(entry.file_size for entry in entries) > MAX_EXPANDED
            or any(
                entry.flag_bits & 1
                or ".." in PurePosixPath(entry.filename).parts
                or entry.filename.startswith(("/", "\\"))
                or re.match(r"^[A-Za-z]:", entry.filename)
                for entry in entries
            )
        ):
            return data, "limited"
        output = io.BytesIO()
        with zipfile.ZipFile(
            output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as target:
            target.comment = source.comment
            for entry in entries:
                # No extraction to disk; ZipInfo metadata, paths and every byte are preserved.
                with source.open(entry) as stream:
                    content = stream.read(min(entry.file_size, MAX_EXPANDED) + 1)
                if len(content) != entry.file_size:
                    return data, "invalid"
                target.writestr(
                    copy.copy(entry), content, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9
                )
        candidate = output.getvalue()
        with zipfile.ZipFile(io.BytesIO(candidate)) as check:
            if [(e.filename, e.file_size, e.CRC) for e in check.infolist()] != [
                (e.filename, e.file_size, e.CRC) for e in entries
            ] or check.testzip() is not None:
                return data, "invalid"
        return candidate, "improved"


def main() -> None:
    # Isolated subprocess in Actions: limit untrusted PDF/archive parsing resources.
    if sys.platform != "win32":
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_INPUT, MAX_INPUT))
    source, destination, extension = sys.argv[1:4]
    data = Path(source).read_bytes()
    candidate, reason = optimize("document." + extension, data)
    Path(destination).write_bytes(candidate)
    print(reason)


if __name__ == "__main__":
    main()
