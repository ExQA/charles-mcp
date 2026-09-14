"""
Utility helpers.

Logging setup, Windows stdio handling and file operations.
"""

import io
import logging
import os
import shutil
import sys
from logging.handlers import RotatingFileHandler


def setup_logging(
    log_file: str = "debug.log",
    level: int = logging.DEBUG,
    max_bytes: int = 5 * 1024 * 1024,  # 5MB
    backup_count: int = 3,
) -> logging.Logger:
    """
    Configure project logging.

    Uses RotatingFileHandler so log files stay small while old logs are kept.

    Args:
        log_file: log file path
        level: log level
        max_bytes: maximum size of one log file in bytes
        backup_count: number of rotated files to keep

    Returns:
        logging.Logger: the configured root logger

    Example:
        >>> logger = setup_logging("app.log", logging.INFO)
        >>> logger.info("Application started")
    """
    # Get the root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Drop existing handlers so they are not added twice
    root_logger.handlers.clear()

    # Create the formatter
    formatter = logging.Formatter(
        fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Create the RotatingFileHandler
    try:
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    except Exception as e:
        # Fall back to stderr if the file handler cannot be created
        print(f"Warning: Cannot create log file '{log_file}': {e}", file=sys.stderr)

    return root_logger


def setup_windows_stdio() -> None:
    """
    Configure standard input/output on Windows.

    Fixes these MCP protocol issues on Windows:
    - automatic newline conversion (\\r\\n vs \\n)
    - dropped connections caused by UTF-8 encoding problems
    - garbled non-ASCII characters

    Note:
        Only has an effect on Windows; elsewhere it does nothing.

    Example:
        >>> setup_windows_stdio()  # call at program start
    """
    if sys.platform != "win32":
        return

    try:
        import msvcrt

        # Binary mode on the descriptors stops Windows from converting newlines
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    except (ImportError, OSError) as e:
        logging.warning(f"cannot set binary mode: {e}")

    # Re-wrap the streams as UTF-8
    try:
        sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8")
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", write_through=True
        )
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
    except Exception as e:
        logging.warning(f"cannot wrap UTF-8 streams: {e}")


def ensure_directory(path: str) -> bool:
    """
    Make sure a directory exists, creating it if needed.

    Args:
        path: directory path

    Returns:
        bool: whether the directory exists or was created

    Example:
        >>> ensure_directory("/tmp/myapp/data")
        True
    """
    try:
        os.makedirs(path, exist_ok=True)
        return True
    except OSError as e:
        logging.error(f"cannot create directory '{path}': {e}")
        return False


def safe_copy_file(src: str, dst: str) -> bool:
    """
    Copy a file safely.

    Args:
        src: source file path
        dst: destination file path

    Returns:
        bool: whether the copy succeeded

    Example:
        >>> safe_copy_file("/tmp/source.txt", "/tmp/backup/source.txt")
        True
    """
    try:
        # Make sure the destination directory exists
        dst_dir = os.path.dirname(dst)
        if dst_dir:
            ensure_directory(dst_dir)

        shutil.copy2(src, dst)
        logging.debug(f"file copied: {src} -> {dst}")
        return True
    except (OSError, shutil.Error) as e:
        logging.error(f"file copy failed '{src}' -> '{dst}': {e}")
        return False


def safe_copy_tree(src: str, dst: str, remove_existing: bool = True) -> bool:
    """
    Copy a directory tree safely.

    Args:
        src: source directory path
        dst: destination directory path
        remove_existing: remove an existing destination first

    Returns:
        bool: whether the copy succeeded

    Example:
        >>> safe_copy_tree("/tmp/source_dir", "/tmp/backup_dir")
        True
    """
    try:
        if remove_existing and os.path.exists(dst):
            shutil.rmtree(dst)

        shutil.copytree(src, dst)
        logging.debug(f"directory copied: {src} -> {dst}")
        return True
    except (OSError, shutil.Error) as e:
        logging.error(f"directory copy failed '{src}' -> '{dst}': {e}")
        return False


def safe_remove_tree(path: str) -> bool:
    """
    Remove a directory tree safely.

    Args:
        path: directory to remove

    Returns:
        bool: whether the removal succeeded

    Example:
        >>> safe_remove_tree("/tmp/to_delete")
        True
    """
    try:
        if os.path.exists(path):
            shutil.rmtree(path)
            logging.debug(f"directory removed: {path}")
        return True
    except (OSError, shutil.Error) as e:
        logging.error(f"directory removal failed '{path}': {e}")
        return False


def get_latest_file(directory: str, extension: str = ".chlsj") -> str | None:
    """
    Return the newest file with the given extension in a directory.

    Args:
        directory: directory path
        extension: file extension, including the dot

    Returns:
        Optional[str]: full path of the newest file, or None if there is none

    Example:
        >>> get_latest_file("/tmp/packages", ".chlsj")
        '/tmp/packages/20260111000741.chlsj'
    """
    try:
        if not os.path.exists(directory):
            return None

        files = [
            f for f in os.listdir(directory) if f.endswith(extension)
        ]

        if not files:
            return None

        # Sort by name (file names are assumed to contain a timestamp)
        sorted_files = sorted(files)
        return os.path.join(directory, sorted_files[-1])
    except OSError as e:
        logging.error(f"failed to find the newest file in '{directory}': {e}")
        return None


def list_files_with_extension(directory: str, extension: str) -> list[str]:
    """
    List all files with the given extension in a directory.

    Args:
        directory: directory path
        extension: file extension, including the dot

    Returns:
        list[str]: file names without the directory

    Example:
        >>> list_files_with_extension("/tmp/packages", ".chlsj")
        ['20260110235654.chlsj', '20260111000222.chlsj']
    """
    try:
        if not os.path.exists(directory):
            return []

        return [f for f in os.listdir(directory) if f.endswith(extension)]
    except OSError as e:
        logging.error(f"failed to list files in '{directory}': {e}")
        return []


def format_bytes(size: int) -> str:
    """
    Format a byte count as a human-readable string.

    Args:
        size: number of bytes

    Returns:
        str: the formatted string

    Example:
        >>> format_bytes(1536)
        '1.50 KB'
        >>> format_bytes(1048576)
        '1.00 MB'
    """
    size_value = float(size)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_value < 1024.0:
            return f"{size_value:.2f} {unit}"
        size_value /= 1024.0
    return f"{size_value:.2f} PB"


def validate_regex(pattern: str) -> tuple[bool, str | None]:
    """
    Check that a regular expression is valid and safe.

    Checks the syntax and rejects patterns that could cause ReDoS.

    Args:
        pattern: the regular expression

    Returns:
        tuple[bool, Optional[str]]: (valid, error message)

    Example:
        >>> validate_regex(r"\\d+")
        (True, None)
        >>> validate_regex(r"[invalid")
        (False, 'unterminated character set at position 0')
    """
    import re

    # Limit the length to keep patterns simple
    max_pattern_length = 500
    if len(pattern) > max_pattern_length:
        return (False, f"regular expression is too long (max {max_pattern_length} characters)")

    # Detect patterns that may cause catastrophic backtracking
    dangerous_patterns = [
        r'\(.*[+*].*\)[+*]',       # nested quantifiers, e.g. (a+)+
        r'\(.*\|.*\)[+*]{2,}',     # alternation followed by repeated quantifiers
    ]
    for dp in dangerous_patterns:
        if re.search(dp, pattern):
            return (False, "regular expression contains nested quantifiers that may be slow")

    try:
        re.compile(pattern)
        return (True, None)
    except re.error as e:
        return (False, str(e))
