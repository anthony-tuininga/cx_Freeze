import ctypes.util
from typing import List

import pefile


def get_pe_dependencies(path: str) -> List[str]:
    """
    For given executable returns list of imported dll's absolute path
    """
    pe = pefile.PE(path, fast_load=True)
    pe.parse_data_directories(directories=[
        pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_IMPORT']],
        import_dllnames_only=True)

    imports = []

    # check if any imports exist
    for entry in getattr(pe, 'DIRECTORY_ENTRY_IMPORT', []):
        # find using current path settings
        dll_path = ctypes.util.find_library(entry.dll.decode())
        # there can be null entries
        if dll_path:
            imports.append(dll_path)

    return imports
