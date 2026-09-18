"""Named security/size limits for the Controlled Workbook v1 compiler (locked
doc sections 4.1.B and 15.3). Every numeric threshold used anywhere in this
module is defined here, once, so a reviewer can audit and tune them without
hunting for magic numbers scattered across files.
"""

COMPILER_VERSION = "rateguard-workbook-v1-compiler/1.0.0"

# Locked doc section 4.1.B: "File-size limit: 10 MiB."
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

# A legitimate small RateGuard workbook (7 required sheets, a few hundred
# rows total) has well under 100 ZIP entries. A few thousand is already
# unreasonable for this contract's scope; reject comfortably below what a
# genuine ZIP-bomb archive needs to matter.
MAX_ZIP_ENTRIES = 2000

# Conservative overall decompression ceiling, independent of any single
# entry's ratio -- a workbook conforming to this contract could never
# legitimately need to expand to more than this much uncompressed content.
MAX_TOTAL_UNCOMPRESSED_BYTES = 200 * 1024 * 1024

# Per-entry compression-ratio ceiling. Ordinary XML/XLSX parts compress at
# roughly 3-10x; 100x is already deep into "crafted to expand explosively"
# territory while leaving generous headroom above legitimate content. Only
# applied to entries above a small floor size, so a handful of bytes of
# highly-compressible padding in a tiny legitimate part can't trip it.
MAX_ENTRY_COMPRESSION_RATIO = 100
MIN_ENTRY_SIZE_FOR_RATIO_CHECK = 1024

# OLE Compound File Binary Format signature. An encrypted OOXML (.xlsx) file
# is actually stored as an OLE/CFB container (the real ZIP payload is
# wrapped inside an encryption stream), so `zipfile.ZipFile` legitimately
# fails to open it with `BadZipFile`. Checking for this exact signature is
# what lets the compiler report "encrypted/password-protected" specifically
# instead of a generic "corrupt archive".
OLE_CFB_SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")

# Standard local-file-header / end-of-central-directory / spanned-archive
# ZIP magic numbers, used to validate that an upload claiming to be .xlsx is
# actually a ZIP container before any ZIP or XML parser touches it.
ZIP_LOCAL_FILE_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")

REQUIRED_EXTENSION = ".xlsx"
