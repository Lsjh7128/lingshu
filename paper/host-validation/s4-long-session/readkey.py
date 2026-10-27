import os,re,sys
raw=open(os.path.expanduser("~/.dsh/.credentials.yaml"),encoding="utf-8").read()
m=re.search(r"^  DEEPSEEK_API_KEY:\s*(\S+)\s*$",raw,re.M)
sys.stdout.write(m.group(1) if m else "")
