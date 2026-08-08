python3 dirsync.py source destination              # basic copy
python3 dirsync.py source destination --dry-run    # preview
python3 dirsync.py source destination --retries 5 --jobs 8
python3 dirsync.py source destination --exclude "*.tmp" --exclude "*.log"