#!/usr/bin/env python3
"""
ctx-prep: A lightweight CLI tool to prepare local file content for LLM context windows.
It parses files/archives into Markdown, counts tokens, and optimizes text via minification/truncation.
"""

import argparse
import sys
import os
import zipfile
from pathlib import Path
import fnmatch
import re
import subprocess

# =============================================================================
# FILE SYSTEM UTILITIES
# =============================================================================

def is_binary(file_path):
    """
    Checks if a file is binary by attempting to read it as text.
    If a UnicodeDecodeError occurs, it's likely a binary file.
    """
    try:
        with open(file_path, 'rt') as check_file:
            check_file.read(1024)
            return False
    except UnicodeDecodeError:
        return True

def should_ignore(path, root_dir=None):
    """
    Determines if a file or directory should be ignored based on common patterns.
    Ignores hidden files (starting with '.'), .git directories, and common
    build/environment artifacts.
    """
    name = os.path.basename(path)
    if name.startswith('.') or name == '.git':
        return True

    # Common binary or irrelevant directories/files to skip
    ignore_patterns = [
        '__pycache__', 'node_modules', 'venv', '.venv',
        '*.pyc', '*.o', '*.a', '*.so', '*.dylib'
    ]
    for pattern in ignore_patterns:
        if fnmatch.fnmatch(name, pattern):
            return True

    return False

# =============================================================================
# MARKDOWN GENERATION (build command)
# =============================================================================

def process_file(file_path, base_path=None):
    """
    Reads a text file and outputs its content as a Markdown code block,
    prefixed with a header indicating the file's path.
    """
    try:
        content = Path(file_path).read_text(errors='replace')
        rel_path = os.path.relpath(file_path, base_path) if base_path else os.path.basename(file_path)
        ext = Path(file_path).suffix.lstrip('.')
        print(f"## File: {rel_path}")
        print(f"```{ext}")
        print(content)
        print("```\n")
    except Exception as e:
        print(f"Error reading {file_path}: {e}", file=sys.stderr)

def process_zip(zip_path):
    """
    Iterates through a ZIP archive and processes non-binary, non-ignored files
    into the same Markdown format as individual files.
    """
    try:
        with zipfile.ZipFile(zip_path, 'r') as z:
            for member in z.infolist():
                if member.is_dir() or should_ignore(member.filename):
                    continue

                with z.open(member) as f:
                    # Read the whole file content to check for binary and then decode
                    data = f.read()
                    # Heuristic check for binary in zip: check for null bytes
                    if b'\0' in data[:1024]:
                        continue
                    try:
                        content = data.decode('utf-8', errors='replace')
                        ext = Path(member.filename).suffix.lstrip('.')
                        print(f"## File: {member.filename}")
                        print(f"```{ext}")
                        print(content)
                        print("```\n")
                    except Exception as e:
                        print(f"Error reading {member.filename} in zip: {e}", file=sys.stderr)
    except Exception as e:
        print(f"Error processing zip {zip_path}: {e}", file=sys.stderr)

def cmd_build(args):
    """
    Implementation of the 'build' command. Recursively walks directories
    and processes files/ZIPs into a single Markdown stream.
    """
    for path_str in args.paths:
        path = Path(path_str)
        if not path.exists():
            print(f"Warning: Path {path_str} does not exist", file=sys.stderr)
            continue

        if path.is_file():
            if path.suffix.lower() == '.zip':
                process_zip(path)
            elif not should_ignore(path) and not is_binary(path):
                process_file(path)
        elif path.is_dir():
            for root, dirs, files in os.walk(path):
                # Filter directories in-place to avoid walking into ignored ones
                dirs[:] = [d for d in dirs if not should_ignore(os.path.join(root, d))]
                for file in files:
                    file_path = os.path.join(root, file)
                    if not should_ignore(file_path) and not is_binary(file_path):
                        # Use parent path as base for relative path calculation
                        process_file(file_path, path.parent)

# =============================================================================
# TOKEN COUNTING (count command)
# =============================================================================

def get_token_count(text):
    """
    Calculates the token count of the given text.
    Uses tiktoken (OpenAI's tokenizer) if available.
    Otherwise, falls back to a heuristic: word count multiplied by 1.3.
    """
    try:
        import tiktoken
        encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text))
    except (ImportError, Exception):
        # Fallback heuristic: 1 word is roughly 1.3 tokens on average
        words = text.split()
        return int(len(words) * 1.3)

def get_clipboard_text():
    """
    Attempts to retrieve text from the system clipboard across different OSs.
    Tries common tools like pbpaste (Mac), xclip/xsel (Linux), or PowerShell (Win).
    """
    # Try pbpaste (macOS)
    try:
        return subprocess.check_output(['pbpaste'], stderr=subprocess.DEVNULL).decode('utf-8')
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # Try xclip (Linux)
    try:
        return subprocess.check_output(['xclip', '-selection', 'clipboard', '-o'], stderr=subprocess.DEVNULL).decode('utf-8')
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # Try xsel (Linux)
    try:
        return subprocess.check_output(['xsel', '--clipboard', '--output'], stderr=subprocess.DEVNULL).decode('utf-8')
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # Try powershell (Windows/WSL)
    try:
        return subprocess.check_output(['powershell.exe', '-Command', 'Get-Clipboard'], stderr=subprocess.DEVNULL).decode('utf-8')
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    return None

def cmd_count(args):
    """
    Implementation of the 'count' command. Reads text from stdin or clipboard
    and outputs the calculated token count.
    """
    text = ""
    # Check if stdin is being piped
    if not sys.stdin.isatty():
        text = sys.stdin.read()

    # If no piped text, try to read from clipboard
    if not text:
        text = get_clipboard_text()

    if not text:
        print("Error: No input provided via stdin or clipboard.", file=sys.stderr)
        sys.exit(1)

    count = get_token_count(text)
    print(f"{count:,} tokens")

# =============================================================================
# CONTEXT OPTIMIZATION (optimize command)
# =============================================================================

def minify_text(text):
    """
    Reduces the text size by removing Markdown comments and empty lines,
    while preserving leading indentation which is crucial for code.
    """
    # Remove markdown comments <!-- ... -->
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)

    # Remove empty lines and trailing whitespace, but preserve leading indentation
    lines = [line.rstrip() for line in text.splitlines()]
    lines = [line for line in lines if line.strip()]

    return "\n".join(lines)

def truncate_middle(text, max_tokens):
    """
    Truncates the text to fit within max_tokens by removing content from the
    middle. This preserves both the beginning (e.g., imports, context) and
    the end (e.g., final logic, conclusions) of the text.
    """
    try:
        import tiktoken
        encoding = tiktoken.get_encoding("cl100k_base")
        tokens = encoding.encode(text)
    except (ImportError, Exception):
        # Fallback to word-based truncation using the 1.3 heuristic
        target_words = int(max_tokens / 1.3)
        words = text.split()
        if len(words) <= target_words:
            return text

        keep_start = int(target_words * 0.6)
        keep_end = target_words - keep_start

        start_part = " ".join(words[:keep_start])
        end_part = " ".join(words[-keep_end:]) if keep_end > 0 else ""
        return f"{start_part}\n\n...[TRUNCATED]...\n\n{end_part}"

    if len(tokens) <= max_tokens:
        return text

    # Middle-out: Keep 60% at start, 40% at end (roughly)
    # The [TRUNCATED] marker and surrounding newlines take about 10 tokens buffer.
    safety_buffer = 10
    if max_tokens <= safety_buffer:
        keep_start = max(0, max_tokens // 2)
        keep_end = max(0, max_tokens - keep_start - 2)
    else:
        keep_start = max(0, int((max_tokens - safety_buffer) * 0.6))
        keep_end = max(0, (max_tokens - safety_buffer) - keep_start)

    start_tokens = tokens[:keep_start]
    end_tokens = tokens[-keep_end:] if keep_end > 0 else []

    start_text = encoding.decode(start_tokens)
    end_text = encoding.decode(end_tokens)

    return f"{start_text}\n\n...[TRUNCATED]...\n\n{end_text}"

def cmd_optimize(args):
    """
    Implementation of the 'optimize' command. Applies minification and
    truncation to fit text into a specified token budget.
    """
    text = ""
    if not sys.stdin.isatty():
        text = sys.stdin.read()

    if not text:
        print("Error: No input provided via stdin for optimize.", file=sys.stderr)
        sys.exit(1)

    # Step 1: Check if it fits as is
    current_tokens = get_token_count(text)
    if current_tokens <= args.max_tokens:
        print(text)
        return

    # Step 2: Try minification
    minified = minify_text(text)
    current_tokens = get_token_count(minified)
    if current_tokens <= args.max_tokens:
        print(minified)
        return

    # Step 3: Fallback to middle-out truncation
    truncated = truncate_middle(minified, args.max_tokens)
    print(truncated)

# =============================================================================
# CLI ENTRY POINT
# =============================================================================

def main():
    """
    Defines the CLI structure using argparse and dispatches to the appropriate
    command handler.
    """
    parser = argparse.ArgumentParser(prog="ctx-prep", description="LLM Context CLI tool")
    subparsers = parser.add_subparsers(dest="command", help="Subcommand to run")

    # Build: local file/directory/zip ingestion
    parser_build = subparsers.add_parser("build", help="Parse files into LLM-ready Markdown")
    parser_build.add_argument("paths", nargs="+", help="Files, directories, or .zip archives")
    parser_build.set_defaults(func=cmd_build)

    # Count: token counting
    parser_count = subparsers.add_parser("count", help="Count tokens from stdin or clipboard")
    parser_count.set_defaults(func=cmd_count)

    # Optimize: fit into context window
    parser_optimize = subparsers.add_parser("optimize", help="Optimize text to fit context window")
    parser_optimize.add_argument("--max-tokens", type=int, required=True, help="Maximum number of tokens")
    parser_optimize.set_defaults(func=cmd_optimize)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Call the associated function for the selected subcommand
    args.func(args)

if __name__ == "__main__":
    main()
