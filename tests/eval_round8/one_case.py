"""给隔离的答题会话展示单题，不加载评分表、历史输出或其他题的画像。"""
import json
from pathlib import Path
import sys

if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    cases = json.loads(Path(__file__).with_name('cases.json').read_text(encoding='utf-8'))
    if len(sys.argv) != 2:
        sys.exit('用法：python one_case.py Q01')
    question = next((row for row in cases if row['id'] == sys.argv[1]), None)
    if question is None:
        sys.exit('没有这个编号')
    print(json.dumps(question, ensure_ascii=False, indent=2))
