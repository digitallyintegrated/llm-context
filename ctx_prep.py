#!/usr/bin/env python3
import argparse
import sys
import os
import zipfile
from pathlib import Path
import fnmatch

def is_binary(file_path):
    """Check if a file is binary."""
    try:
        with open(file_path, 'rt') as check_file:
            check_file.read(1024)
            return False
    except UnicodeDecodeError:
        return True

def should_ignore(path, root_dir=None):
    """Check if a path should be ignored."""
    name = os.path.basename(path)
    if name.startswith('.') or name == '.git':
        return True

    # Common binary or irrelevant directories/files
    ignore_patterns = ['__pycache__', 'node_modules', 'venv', '.venv', '*.pyc', '*.o', '*.a', '*.so', '*.dylib']
    for pattern in ignore_patterns:
        if fnmatch.fnmatch(name, pattern):
            return True

    return False

def process_file(file_path, base_path=None):
    """Generate Markdown for a single file."""
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
    """Generate Markdown for files in a zip archive."""
    try:
        with zipfile.ZipFile(zip_path, 'r') as z:
            for member in z.infolist():
                if member.is_dir() or should_ignore(member.filename):
                    continue

                with z.open(member) as f:
                    # Read the whole file content
                    data = f.read()
                    # Heuristic check for binary in zip
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
                dirs[:] = [d for d in dirs if not should_ignore(os.path.join(root, d))]
                for file in files:
                    file_path = os.path.join(root, file)
                    if not should_ignore(file_path) and not is_binary(file_path):
                        process_file(file_path, path.parent)

import re
import subprocess

def get_token_count(text):
    """Count tokens using tiktoken with heuristic fallback."""
    try:
        import tiktoken
        encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text))
    except (ImportError, Exception):
        # Fallback: word count * 1.3
        words = text.split()
        return int(len(words) * 1.3)

def get_clipboard_text():
    """Try to get text from the clipboard using various system tools."""
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
    text = ""
    if not sys.stdin.isatty():
        text = sys.stdin.read()

    if not text:
        text = get_clipboard_text()

    if not text:
        print("Error: No input provided via stdin or clipboard.", file=sys.stderr)
        sys.exit(1)

    count = get_token_count(text)
    print(f"{count:,} tokens")

def minify_text(text):
    """Programmatically minify text by removing extra whitespace, comments, and empty lines."""
    # Remove markdown comments <!-- ... -->
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)

    # Remove empty lines and trailing whitespace, but preserve leading indentation
    lines = [line.rstrip() for line in text.splitlines()]
    lines = [line for line in lines if line.strip()]

    # Join with single newline
    return "\n".join(lines)

def truncate_middle(text, max_tokens):
    """Apply middle-out truncation to fit within max_tokens."""
    # We need to work with tokens for accuracy
    try:
        import tiktoken
        encoding = tiktoken.get_encoding("cl100k_base")
        tokens = encoding.encode(text)
    except (ImportError, Exception):
        # Fallback to simple split if tiktoken fails
        # Use 1.3x heuristic: target_words = max_tokens / 1.3
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

    keep_start = int(max_tokens * 0.6)
    keep_end = max_tokens - keep_start

    # Approximate space for [TRUNCATED] marker (a few tokens)
    # Reducing keeping amounts slightly to be safe
    # The [TRUNCATED] marker and surrounding newlines take about 5-10 tokens.
    safety_buffer = 10
    if max_tokens <= safety_buffer:
        # Extremely small budget, just take a few from start and end
        keep_start = max(0, max_tokens // 2)
        keep_end = max(0, max_tokens - keep_start - 2) # Extra cautious
    else:
        keep_start = max(0, int((max_tokens - safety_buffer) * 0.6))
        keep_end = max(0, (max_tokens - safety_buffer) - keep_start)

    start_tokens = tokens[:keep_start]
    end_tokens = tokens[-keep_end:] if keep_end > 0 else []

    start_text = encoding.decode(start_tokens)
    end_text = encoding.decode(end_tokens)

    return f"{start_text}\n\n...[TRUNCATED]...\n\n{end_text}"

def cmd_optimize(args):
    text = ""
    if not sys.stdin.isatty():
        text = sys.stdin.read()

    if not text:
        print("Error: No input provided via stdin for optimize.", file=sys.stderr)
        sys.exit(1)

    # 1. Check current tokens
    current_tokens = get_token_count(text)
    if current_tokens <= args.max_tokens:
        print(text)
        return

    # 2. Minify
    minified = minify_text(text)
    current_tokens = get_token_count(minified)
    if current_tokens <= args.max_tokens:
        print(minified)
        return

    # 3. Truncate
    truncated = truncate_middle(minified, args.max_tokens)
    print(truncated)

def main():
    parser = argparse.ArgumentParser(prog="ctx-prep", description="LLM Context CLI tool")
    subparsers = parser.add_subparsers(dest="command", help="Subcommand to run")

    # Build subcommand
    parser_build = subparsers.add_parser("build", help="Parse files into LLM-ready Markdown")
    parser_build.add_argument("paths", nargs="+", help="Files, directories, or .zip archives")
    parser_build.set_defaults(func=cmd_build)

    # Count subcommand
    parser_count = subparsers.add_parser("count", help="Count tokens from stdin or clipboard")
    parser_count.set_defaults(func=cmd_count)

    # Optimize subcommand
    parser_optimize = subparsers.add_parser("optimize", help="Optimize text to fit context window")
    parser_optimize.add_argument("--max-tokens", type=int, required=True, help="Maximum number of tokens")
    parser_optimize.set_defaults(func=cmd_optimize)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)

if __name__ == "__main__":
    main()
