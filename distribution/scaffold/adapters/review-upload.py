#!/usr/bin/env python3
"""User-configured explicit review upload hook. No built-in external storage account."""
import os,sys,subprocess,json,shlex
hook=os.environ.get('STORY_REVIEW_UPLOAD_COMMAND')
if not hook:raise SystemExit('Set STORY_REVIEW_UPLOAD_COMMAND to your uploader; it must print URL: https://... ; local file review is also supported')
# An explicit user command; subprocess argument list, no shell evaluation.
raise SystemExit(subprocess.run(shlex.split(hook)+sys.argv[1:]).returncode)
